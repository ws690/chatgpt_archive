const leaseMs=30*60*1000;
let queue=Promise.resolve();
const serialize=fn=>{const p=queue.then(fn,fn);queue=p.catch(()=>{});return p;};
async function openRun(limit,mode){await chrome.tabs.create({url:chrome.runtime.getURL(`runner.html?limit=${limit}&mode=${mode}`),active:mode!=='daily'});}
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(sender.id!==chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL('')))return;
  // Schedule short waits outside the hidden runner tab and outside the lock queue.
  if(m.kind==='delay'){
    const valid=new URL(sender.url).pathname==='/runner.html' &&
      Number.isInteger(m.ms) && m.ms>=0 && m.ms<=5000;
    if(!valid){reply({ok:false});return;}
    setTimeout(()=>reply({ok:true}),m.ms);
    return true;
  }
  serialize(async()=>{
    if(m.kind==='open'){await openRun(m.limit===3?3:50,m.full===true?'full':'manual');return {ok:true};}
    if(m.kind==='enable'){
      await chrome.storage.local.set({dailyEnabled:m.enabled===true});
      return {ok:true};
    }
    if(m.kind==='claim'){
      const s=await chrome.storage.local.get(['run','dailyEnabled']);
      if(new URL(sender.url).searchParams.get('mode')==='daily' && s.dailyEnabled!==true)
        return {ok:false,error:'daily_paused'};
      if(s.run?.expires>Date.now())return {ok:false,error:'another_run_active'};
      const run={id:crypto.randomUUID(),expires:Date.now()+leaseMs,tab:sender.tab?.id};
      await chrome.storage.local.set({run,status:{status:'running',at:new Date().toISOString()}});return {ok:true,run};
    }
    if(m.kind==='alive'){
      const {run}=await chrome.storage.local.get('run');
      const now=Date.now();
      if(run?.id!==m.id || run.expires<=now)return {ok:false};
      if(run.expires-now<leaseMs-60000){
        run.expires=now+leaseMs;
        await chrome.storage.local.set({run});
      }
      return {ok:true};
    }
    if(m.kind==='finish'){
      const {run}=await chrome.storage.local.get('run');
      if(run?.id===m.id){await chrome.storage.local.set({status:m.status});await chrome.storage.local.remove('run');}
      return {ok:true};
    }
    return {ok:false};
  }).then(reply,()=>reply({ok:false,error:'page_or_browser_error'}));
  return true;
});
