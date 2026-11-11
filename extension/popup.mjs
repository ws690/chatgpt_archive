import {HOST} from './core.mjs';
const out=document.querySelector('#status');
async function refresh(){const s=await chrome.storage.local.get(['status','dailyEnabled']);document.querySelector('#schedule').textContent=s.dailyEnabled?'Daily: enabled, 00:00 Europe/London':'Daily: paused';out.textContent=JSON.stringify(s.status||{status:'not_run'},null,2);}
for(const [id,limit] of [['test',3],['run',50]])document.querySelector('#'+id).onclick=async()=>{await chrome.runtime.sendMessage({kind:'open',limit});window.close();};
document.querySelector('#full').onclick=async()=>{await chrome.runtime.sendMessage({kind:'open',limit:50,full:true});window.close();};
for(const [id,enabled] of [['enable',true],['pause',false]])document.querySelector('#'+id).onclick=async()=>{await chrome.runtime.sendMessage({kind:'enable',enabled});await refresh();};
document.querySelector('#ping').onclick=async()=>{try{const r=await chrome.runtime.sendNativeMessage(HOST,{cmd:'ping'});out.textContent=r.ok?'NAS writer is ready.':'NAS writer needs attention.';}catch{out.textContent='NAS writer unavailable. Check installation.';}};
refresh();
