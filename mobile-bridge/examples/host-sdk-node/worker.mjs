import {HostClient,serve} from './nexus-host.mjs';
import {renderPage} from './page.mjs';
const host=new HostClient();
const schema={type:'object',additionalProperties:true};
async function job(args){
 setImmediate(async()=>{try{for(let i=(args.checkpoint?.i||0);i<args.input.count;i++){
  const state=await host.jobs.get({job_id:args.job_id});if(!['running','recovering'].includes(state.status))return;
  await host.jobs.report({job_id:args.job_id,attempt:args.attempt,checkpoint:{i:i+1},progress:{done:i+1,total:args.input.count}});
  await new Promise(r=>setTimeout(r,100));
 }await host.jobs.report({job_id:args.job_id,attempt:args.attempt,status:'completed',result:{count:args.input.count,recovered:args.recovery}});
 }catch(error){await host.jobs.report({job_id:args.job_id,attempt:args.attempt,status:'failed',error:error.message}).catch(()=>{});}});
 return {accepted:true};
}
async function call(action,values={}) {
  if(action==='models')return host.models.list();
  if(action==='history')return host.history.snapshot({limit:5});
  if(action==='echo')return host.services.call({name:'host-sdk-node.echo',values});
  if(action==='stream'){const events=[];for await(const e of host.models.stream(values))events.push(e);return events;}
  if(action==='generate')return host.models.generate(values);
  if(action==='dialogue')return host.dialogues.create({...values,persona:'只简短回答，不执行自主任务。',title:'Node 示例独立对话'});
  if(action==='send')return host.dialogues.send(values);
  if(action==='continue')return host.dialogues.continue_turn(values);
  if(action==='job')return host.jobs.submit({name:'counter',input:{count:20},recoverable:true,request_id:values.request_id});
  return {capabilities:await host.capabilities.describe(),jobs:await host.jobs.list(),dialogues:await host.dialogues.list()};
}
serve({tools:['page','echo','job'].map(name=>({name,description:name,inputSchema:schema})),
 initialize:async()=>{await host.capabilities.require({requires:{models:1,jobs:1,services:1}});await host.services.register({name:'echo',version:1,target:{service:'worker',tool:'echo'}});await host.jobs.register({name:'counter',version:1,target:{service:'worker',tool:'job'},recover_target:{service:'worker',tool:'job'}});},
 handlers:{job,echo:async args=>({echo:args.values,caller:args.caller_plugin_id}),
   page:({action,values={}})=>action==='ui'?renderPage(host,call,values):call(action,values)}});
