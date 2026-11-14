const idleLimitMs=5*60*1000;
const heartbeatIntervalMs=30000;
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
      const {dailyEnabled}=await chrome.storage.local.get('dailyEnabled');
      const {run:activeRun}=await chrome.storage.session.get('run');
      if(new URL(sender.url).searchParams.get('mode')==='daily' && dailyEnabled!==true)
        return {ok:false,error:'daily_paused'};
      if(activeRun){
        const tabs=await chrome.tabs.query({url:chrome.runtime.getURL('runner.html')+'*'});
        if(tabs.some(tab=>tab.id===activeRun.tab)){
          if(activeRun.tab===sender.tab?.id || Date.now()-activeRun.lastSeen<idleLimitMs)
            return {ok:false,error:'another_run_active'};
          await chrome.tabs.remove(activeRun.tab);
        }
      }
      const run={id:crypto.randomUUID(),tab:sender.tab?.id,lastSeen:Date.now()};
      await chrome.storage.session.set({run});return {ok:true,run};
    }
    if(m.kind==='alive'){
      let run;
      try{({run}=await chrome.storage.session.get('run'));}
      catch{return {ok:false,lease:'read_failed'};}
      if(!run)return {ok:false,lease:'missing'};
      if(run.id!==m.id || run.tab!==sender.tab?.id)return {ok:false,lease:'replaced'};
      if(Date.now()-run.lastSeen>=heartbeatIntervalMs){
        run.lastSeen=Date.now();
        try{await chrome.storage.session.set({run});}
        catch{return {ok:false,lease:'write_failed'};}
      }
      return {ok:true};
    }
    if(m.kind==='finish'){
      const {run}=await chrome.storage.session.get('run');
      if(run?.id===m.id){await chrome.storage.session.remove('run');await chrome.storage.local.set({status:m.status});}
      return {ok:true};
    }
    return {ok:false};
  }).then(reply,()=>reply({ok:false,error:'page_or_browser_error'}));
  return true;
});
