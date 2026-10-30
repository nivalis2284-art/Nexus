// Nexus Host SDK 1.1.0 — Node.js 22+, no npm dependencies.
import {AsyncLocalStorage} from 'node:async_hooks';
import {createInterface} from 'node:readline';
const context=new AsyncLocalStorage();
const longMethods=new Set(['models.generate','models.embed','dialogues.send','dialogues.continue','dialogues.continue_turn','services.call']);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export class HostClient {
 constructor({url=process.env.NEXUS_HOST_URL,token=process.env.NEXUS_PLUGIN_TOKEN}={}) {
  this.url=url;this.token=token;
  const namespace=path=>new Proxy((params={})=>path==='models.stream'?this.stream(params):this.call(path,params),{get:(_,name)=>name==='then'?undefined:namespace(path?`${path}.${String(name)}`:String(name))});
  for(const name of ['capabilities','models','dialogues','history','jobs','storage','services','context','events','hooks','chat','mcp','operations'])this[name]=namespace(name);
 }
 async request(method,params={}) {
  const response=await fetch(this.url,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${this.token}`},body:JSON.stringify({method,params}),signal:AbortSignal.timeout(30000)});
  const value=await response.json();if(!response.ok)throw Error(value.error?.message||'Host request failed');return value.result;
 }
 async call(method,params={}) {
  if(method==='services.call'&&context.getStore()?.service)params={...params,context_id:context.getStore().service};
  if(longMethods.has(method)&&context.getStore()?.job)params={...params,job_id:context.getStore().job,job_attempt:context.getStore().attempt};
  if(!longMethods.has(method))return this.request(method,params);
  const {operation_id}=await this.request('operations.start',{method,params});
  for(;;){const op=await this.request('operations.get',{operation_id});if(op.status==='completed')return op.result;if(!['pending','running'].includes(op.status))throw Error(op.error||op.status);await delay(50);}
 }
 async *stream(params) {
  if(context.getStore()?.job)params={...params,job_id:context.getStore().job,job_attempt:context.getStore().attempt};
  const {operation_id}=await this.request('operations.start',{method:'models.generate',params});let after=0;
  try{for(;;){const page=await this.request('operations.events',{operation_id,after});after=page.cursor;
   for(const event of page.events){if(event.type==='model.event')yield event.data;if(event.type==='operation.done'){const op=await this.request('operations.get',{operation_id});if(op.status!=='completed')throw Error(op.error);yield {type:'complete',result:op.result};return;}}
   await delay(50);
  }}finally{await this.request('operations.cancel',{operation_id}).catch(()=>{});}
 }
}
export function serve({tools,handlers,initialize}) {
 const input=createInterface({input:process.stdin,crlfDelay:Infinity});
 input.on('line',line=>{let message;try{message=JSON.parse(line);}catch{return;}if(message.id===undefined)return;
  (async()=>{const p=message.params||{};let result;
   if(message.method==='initialize'){await initialize?.();result={protocolVersion:p.protocolVersion||'2025-06-18',serverInfo:{name:'nexus-sdk-node',version:'1.1.0'},capabilities:{tools:{}}};}
   else if(message.method==='tools/list')result={tools};else if(message.method==='ping')result={};
   else if(message.method==='tools/call'){const args={...(p.arguments||{})};const token=args._host_context;delete args._host_context;const value=await context.run({service:token,job:args.job_id,attempt:args.attempt},()=>handlers[p.name](args));result={content:[{type:'text',text:JSON.stringify(value)}],isError:false};}
   else throw Error('Unsupported method');
   process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:message.id,result})+'\n');
  })().catch(error=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:message.id,result:{content:[{type:'text',text:error.message}],isError:true}})+'\n'));
 });
}
