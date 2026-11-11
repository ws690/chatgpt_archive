import {HOST,ORIGIN,inspectPage,validChatPath,chatId,safeError} from './core.mjs';
import {collectDocuments} from './documents.mjs';
import {saveChatImages} from './chat-files.mjs';
import {collectHistory,mergeEarlier} from './history.mjs';
import {chatFingerprint,isUnchanged,transcriptFingerprint,rotateChats,usesIncrementalMode} from './checkpoints.mjs';
const display=document.querySelector('#status');
const sleep=async ms=>{
 const result=await chrome.runtime.sendMessage({kind:'delay',ms}).catch(()=>null);
 if(!result?.ok)await new Promise(resolve=>setTimeout(resolve,ms));
};
let cancelled=false,tabId,runId,runExpires,keepTab=false,ownedTab=false;
const humanStep=reason=>['authentication_required','authentication_challenge','route_error'].includes(reason);
const authPage=value=>/^https:\/\/(?:auth\.openai\.com(?:\/|$)|chatgpt\.com\/(?:auth|log-in)(?:\/|$))/i.test(value||'');
const sameChat=(left,right)=>left===right||(validChatPath(left)&&validChatPath(right)&&chatId(left)===chatId(right));
document.querySelector('#cancel').onclick=()=>{cancelled=true;};
async function alive({publishing=false}={}){
 if(cancelled)throw Error('run_cancelled');
 // Leave enough time to publish captured chats before another run can claim the lock.
 if(!publishing && Date.now()>=runExpires-120000)throw Error('run_timeout');
 if(!(await chrome.runtime.sendMessage({kind:'alive',id:runId})).ok)throw Error('run_timeout');
}
async function native(message){try{return await chrome.runtime.sendNativeMessage(HOST,message);}catch{throw Error('local_writer_unavailable');}}
async function closeRunner(){const tab=await chrome.tabs.getCurrent();if(tab)await chrome.tabs.remove(tab.id);}
async function state(mode,offset){
 await alive();
 for(let attempt=0;attempt<7;attempt++){
  let result;
  try{result=await chrome.scripting.executeScript({target:{tabId},func:inspectPage,args:offset===undefined?[mode]:[mode,offset]});}
  catch(error){
   const tab=await chrome.tabs.get(tabId);
   if(authPage(tab.url)||/https:\/\/auth\.openai\.com\//i.test(error.message))throw Error('authentication_required');
   if(attempt<6 && tab.url?.startsWith(ORIGIN+'/') &&
      /frame|context|document|tab was closed/i.test(error.message)){
    await sleep(1500);
    continue;
   }
   throw Error('chatgpt_unreachable');
  }
  const s=result[0]?.result;if(!s)throw Error('page_or_browser_error');
  // Allow ordinary automatic page verification to finish; never click/solve it.
  if(s.error==='authentication_challenge' && attempt<6){await sleep(2000);await alive();continue;}
  if(s.error)throw Object.assign(Error(s.error),{checks:s.checks});return s;
 }
}
async function navigate(url){
 await alive();
 await chrome.tabs.update(tabId,{url});
 const target=new URL(url), deadline=Date.now()+60000;
 while(Date.now()<deadline){
  await alive();
  const tab=await chrome.tabs.get(tabId);
  if(authPage(tab.url))throw Error('authentication_required');
  if(tab.url?.startsWith('chrome-error:'))throw Error('chatgpt_unreachable');
  if(tab.status==='complete' && tab.url){
   const landed=new URL(tab.url);
   if(landed.origin===target.origin && sameChat(landed.pathname,target.pathname)){await sleep(2500);return;}
  }
  await sleep(500);
 }
 throw Error('chatgpt_unreachable');
}
async function messages(){
 let previous, stableMessages, latestReadable, foundMessages=false, assistantExpected=false;
 const started=Date.now();
 const deadline=started+90000;
 while(Date.now()<deadline){
  const sample=await state('messages');
  const snapshot=JSON.stringify(sample.messages);
  assistantExpected ||= sample.assistantExpected;
  stableMessages=undefined;
  if(sample.messages.length){
   foundMessages=true;
   if(!sample.streaming && sample.messages.some(({role})=>role==='assistant'))latestReadable=sample;
   if(!sample.streaming && !sample.pending && previous===snapshot){
    stableMessages=sample;
    const replies=stableMessages.messages.filter(({role})=>role==='assistant');
    if(replies.some(({text})=>text!=='[Image attachment]') ||
       (replies.length && Date.now()-started>=12000))return stableMessages;
   }
   previous=sample.streaming?undefined:snapshot;
  }else previous=undefined;
  await sleep(2000);
 }
 if(stableMessages && !assistantExpected)return stableMessages;
 if(latestReadable)return {...latestReadable,unsettled:true};
 throw Error(foundMessages?'conversation_not_stable':'no_messages');
}
async function main(){
 const scheduled=new URL(location.href).searchParams.get('mode')==='daily';
 const claim=await chrome.runtime.sendMessage({kind:'claim'});
 if(!claim.ok){
  display.textContent=claim.error==='daily_paused'?'Daily collection is paused.':'Another run is already active.';
  if(scheduled){
   try{if(claim.error==='daily_paused')await native({cmd:'schedule',state:'paused'});}
   finally{await closeRunner();}
  }
  return;
 }
 runId=claim.run.id;
 runExpires=claim.run.expires;
 const documents={saved:0,already_saved:0,skipped:0,deferred:0};
 let previousPhase,lastProgress=0,attemptedChats=0;
 const progress=async(phase,turns)=>{
  const now=Date.now();
  if(phase===previousPhase && now-lastProgress<15000)return;
  previousPhase=phase;
  lastProgress=now;
  await native({cmd:'progress',phase,chats:attemptedChats,turns,documents}).catch(()=>{});
 };
 let result={status:'attention_required',reason:'page_or_browser_error',at:new Date().toISOString()};
 try{
  await native({cmd:'status',report:{status:'running',reason:'running'}});
  if(scheduled){const ack=await native({cmd:'schedule',state:'started'});if(!ack.ok)throw Error('local_writer_unavailable');}
  const ping=await native({cmd:'ping'});if(!ping.ok || !ping.free_space_ok)throw Error('local_writer_unavailable');
  await progress('discovery');
  const params=new URL(location.href).searchParams;
  const target=params.get('chat');
  if(target && !validChatPath(target))throw Error('page_or_browser_error');
  const seen=new Map();let stable=0,stop='scroll_limit',scope='sidebar';
  if(target){
   seen.set(target,params.get('title')?.trim().slice(0,1024)||'Untitled');
   stop=scope='requested_chat';
  }else{
   const existing=await chrome.tabs.query({url:ORIGIN+'/*'});
   const candidates=existing.filter(t=>validChatPath(new URL(t.url).pathname)||new URL(t.url).pathname==='/')
    .sort((a,b)=>(b.lastAccessed||0)-(a.lastAccessed||0));
   if(candidates.length){
    let source, lastError;
    for(const candidate of candidates.slice(0,8)){
     tabId=candidate.id;
     try{
      const sample=await state('links');
      source ||= candidate;
      if(sample.scope==='sidebar'&&sample.links.length){source=candidate;break;}
     }catch(error){
      if(['run_cancelled','run_timeout'].includes(safeError(error)))throw error;
      if(!lastError||humanStep(safeError(error)))lastError=error;
     }
    }
    if(!source)throw lastError||Error('chatgpt_unreachable');
    tabId=source.id;
    await chrome.tabs.update(tabId,{active:true});
    await sleep(1500);
   }
   else{const tab=await chrome.tabs.create({url:'about:blank',active:true});tabId=tab.id;ownedTab=true;await navigate(ORIGIN+'/');}
   await state('prepare');await sleep(1500);
   const limit=params.get('limit')==='3'?3:50;
   const discoveryLimit=limit===3?3:200;
   const sidebarDeadline=Date.now()+45000;
   const chatIds=new Set();
   let sidebarSeen=false;
   const projects=new Set();
   for(let i=0;i<20;i++){
    const before=seen.size;const s=await state('links');
    for(const project of s.projects || [])projects.add(project);
    sidebarSeen ||= s.scope==='sidebar'&&s.links.length>0;
    scope=sidebarSeen?'sidebar':s.scope;
    for(const a of s.links){
     if(!validChatPath(a.path))continue;
     const id=chatId(a.path);
     if(chatIds.has(id))continue;
     chatIds.add(id);seen.set(a.path,a.title);
     if(seen.size>=discoveryLimit)break;
    }
    display.textContent=`Discovered ${seen.size} chats (scan limit ${discoveryLimit}).`;
    if(seen.size>=discoveryLimit){stop='chat_limit';break;}
    stable=seen.size===before?stable+1:0;
    if(seen.size && stable>=3 && (sidebarSeen||Date.now()>=sidebarDeadline)){stop='sidebar_stable_not_proof_of_completeness';break;}
    if(sidebarSeen){await state('scroll');await sleep(1500);}
    else{await state('prepare');await sleep(3000);}
   }
   if(limit===50 && projects.size){
    const projectQueue=new Map();
    for(const project of [...projects].slice(0,10)){
     try{
      if(ownedTab)await chrome.tabs.remove(tabId);
      const tab=await chrome.tabs.create({url:'about:blank',active:true});
      tabId=tab.id;ownedTab=true;
      await navigate(ORIGIN+project);
      let page;
      for(let attempt=0; attempt<10; attempt++){
       page=await state('project-links');
       if(page.links.length)break;
       await sleep(3000);
      }
      for(const link of page.links){
       if(!validChatPath(link.path))continue;
       const id=chatId(link.path);
       if(!chatIds.has(id))projectQueue.set(id,link);
      }
     }catch(error){
      const code=safeError(error);
      if(humanStep(code)||['run_timeout','run_cancelled'].includes(code))throw error;
     }
    }
    for(const link of projectQueue.values())seen.set(link.path,link.title);
   }
   if(!seen.size){await state('links');throw Error('no_visible_chats');}
  }
  const incremental=usesIncrementalMode(params);
  const {chatCheckpoints={}}=await chrome.storage.local.get('chatCheckpoints');
  const updatedCheckpoints={};
  const saveCheckpoints=async()=>{
   if(!Object.keys(updatedCheckpoints).length)return;
   const entries=Object.entries({...chatCheckpoints,...updatedCheckpoints})
    .sort((a,b)=>(b[1].checkedAt||0)-(a[1].checkedAt||0)).slice(0,2000);
   await chrome.storage.local.set({chatCheckpoints:Object.fromEntries(entries)});
  };
  const captures=[];let failed=0,unchanged=0,totalBytes=0,reason;
  let networkFailures=0;
  const images={saved:0,already_saved:0,skipped:0};
  let remainingImages=50;
  const documentBudget={left:target?24:12};
  const rotating=!target&&params.get('limit')!=='3';
  const {lastArchiveAttempt}=rotating?await chrome.storage.local.get('lastArchiveAttempt'):{};
  const batch=rotating?rotateChats([...seen],lastArchiveAttempt,50,chatId):[...seen];
  for(const [path,title] of batch){
   try{
    await alive();
    attemptedChats++;
    await progress('opening_chat');
    if(rotating)await chrome.storage.local.set({lastArchiveAttempt:chatId(path)});
    if(ownedTab)await chrome.tabs.remove(tabId);
    const tab=await chrome.tabs.create({url:'about:blank',active:true});
    tabId=tab.id;ownedTab=true;
    await navigate(ORIGIN+path);
    const s=await state('links');
    if(!sameChat(s.path,path))throw Error('chatgpt_unreachable');
    networkFailures=0;
    const id=chatId(path);
    const savedDocuments=new Set(chatCheckpoints[id]?.documents || []);
    const seenDocuments=new Set();
    const deferredDocuments=new Set();
    const attemptedDocuments=new Set(chatCheckpoints[id]?.documentAttempts || []);
    const documentKeys=()=>[...savedDocuments].slice(-200);
    const documentAttempts=()=>[...attemptedDocuments].slice(-200);
    const saveDocuments=async(includeOutputs=false,phase='outputs',maxAttempts=Infinity)=>{
     try{
      await progress(phase);
      await collectDocuments({
       tabId,path:s.path,id,title,saved:savedDocuments,seen:seenDocuments,
       attempted:attemptedDocuments,deferred:deferredDocuments,report:documents,budget:documentBudget,check:alive,includeOutputs,maxAttempts,sleep
      });
     }finally{
      updatedCheckpoints[id]={...(updatedCheckpoints[id]||chatCheckpoints[id]),documents:documentKeys(),documentAttempts:documentAttempts()};
     }
    };
    let initial;
    try{
     await progress('stabilizing');
     initial=await messages();
     if(initial.view.top+initial.view.viewport<initial.view.height-5){
      await state('history-scroll',initial.view.height);
      await sleep(1500);
      initial=await messages();
     }
    }catch(error){
     if(['conversation_not_stable','no_messages'].includes(safeError(error))){
      await saveDocuments(true);
     }
     throw error;
    }
    const fingerprint=await chatFingerprint(title,initial);
    if(incremental && !initial.unsettled && isUnchanged(chatCheckpoints[id],fingerprint)){
     await saveDocuments(true);
     unchanged++;
     display.textContent=`Captured ${captures.length} of ${seen.size}; ${unchanged} unchanged; ${failed} errors.`;
     continue;
    }
    const makeCapture=(snapshot,history)=>({id,title,history,
     messages:snapshot.messages.map(({role,text})=>({role,text}))});
    // Retain the readable window if a file viewer or later navigation fails.
    const captureIndex=captures.length;
    const draft=makeCapture(initial,'stalled');
    const encoder=new TextEncoder();
    const draftBytes=encoder.encode(JSON.stringify(draft)).length;
    if(totalBytes+draftBytes>12*1024*1024)throw Error('oversized_conversation');
    captures.push(draft);totalBytes+=draftBytes;
    const seenImages=new Set();
    const saveImages=async()=>{
     if(!remainingImages)return;
     try{
      const result=await saveChatImages({tabId,path:s.path,id,title,remaining:remainingImages,check:alive,seen:seenImages});
      for(const key of Object.keys(images))images[key]+=result[key];
      remainingImages=Math.max(0,remainingImages-result.saved-result.already_saved-result.skipped);
     }catch(e){
      if(['run_cancelled','run_timeout'].includes(safeError(e)))throw e;
      images.skipped++;
     }
    };
    await progress('history',initial.messages.length);
    const history=await collectHistory(initial,{
     read:async()=>{const window=await state('messages');await progress('history',window.messages.length);return window;},
     scroll:offset=>state('history-scroll',offset),pause:sleep
    });
    const needsTailCheck=history.history==='visible_start' &&
      (initial.unsettled || initial.view.top+initial.view.viewport<initial.view.height-5);
    if(needsTailCheck)history.history='stalled';
    let extraBytes=0;
    const retainHistory=()=>{
     const capture=makeCapture(history,history.history);
     const sizeChange=encoder.encode(JSON.stringify(capture)).length-draftBytes-extraBytes;
     if(totalBytes+sizeChange>12*1024*1024)throw Error('oversized_conversation');
     captures[captureIndex]=capture;totalBytes+=sizeChange;extraBytes+=sizeChange;
    };
    retainHistory();
    if(history.failure)throw Error(history.failure);
    if(needsTailCheck){
     const start=await state('messages');
     await state('history-scroll',start.view.height);
     await sleep(1500);
     await progress('stabilizing');
     const tail=await messages();
     if(!tail.unsettled && tail.view.top+tail.view.viewport>=tail.view.height-5){
      const joined=mergeEarlier(tail.messages,history.messages);
      if(joined){history.messages=joined;history.history='visible_start';retainHistory();}
     }
     await state('history-scroll',start.view.top);
     await sleep(1500);
    }
    const complete=history.history==='visible_start';
    const full=complete ? await transcriptFingerprint(title,captures[captureIndex].messages) : undefined;
    if(complete)updatedCheckpoints[id]={fingerprint,full,checkedAt:Date.now(),
     documents:documentKeys(),documentAttempts:documentAttempts()};
    // Keep the verified transcript before opening viewers that may change the page.
    for(let window=0; window<480 && (documentBudget.left>0 || remainingImages>0); window++){
     const before=await state('messages');
     await saveImages();
     await saveDocuments(true,'outputs',2);
     if(before.view.top+before.view.viewport>=before.view.height-5)break;
     const step=Math.max(100,Math.floor(before.view.viewport*0.75));
     await state('history-scroll',before.view.top+step);
     await sleep(700);
     const after=await state('messages');
     if(after.view.top<=before.view.top && after.view.height===before.view.height)break;
    }
    if(documentBudget.left>0){
     const current=await state('messages');
     await state('history-scroll',current.view.height);
     await sleep(1500);
     await saveDocuments(true);
    }
    const checkpoint={fingerprint,full,checkedAt:Date.now(),documents:documentKeys(),documentAttempts:documentAttempts()};
    if(incremental && complete && chatCheckpoints[id]?.full===full){
     updatedCheckpoints[id]=checkpoint;
     captures.pop();totalBytes-=draftBytes+extraBytes;
     unchanged++;
     continue;
    }
    if(complete)updatedCheckpoints[id]=checkpoint;
   }catch(e){
    failed++;
    const current=safeError(e);
    if(!reason||humanStep(current)||current==='chatgpt_unreachable')reason=current;
    if(current==='chatgpt_unreachable')networkFailures++;
    if(humanStep(current))keepTab=true;
    if(humanStep(current)||networkFailures>=2||['run_cancelled','run_timeout'].includes(current))break;
   }
   display.textContent=`Captured ${captures.length} of ${seen.size}; ${unchanged} unchanged; ${failed} errors.`;await sleep(3000);
  }
  if(!captures.length && unchanged && !failed){
   const reply=await native({cmd:'status',report:{status:'up_to_date',unchanged,documents}});
   if(!reply.ok)throw Error('native_publish_failed');
   result=reply.report;
   display.textContent=JSON.stringify(result,null,2);
   await saveCheckpoints();
   return;
  }
  if(!captures.length){
   await saveCheckpoints();
   throw Error(reason||'no_messages');
  }
  await alive({publishing:true});
  await progress('publishing');
  const report={discovered:seen.size,failed,reason,unchanged,discovery_stop:stop,discovery_scope:scope,account_complete:false,images,documents};
  const published=await native({cmd:'publish',captures,report});if(!published.ok)throw Error('native_publish_failed');
  result=published.report;display.textContent=JSON.stringify(result,null,2);
  await saveCheckpoints();
 }catch(e){if(humanStep(safeError(e)))keepTab=true;result={status:'attention_required',reason:safeError(e),at:new Date().toISOString(),documents,...(e.checks?{checks:e.checks}:{})};display.textContent=JSON.stringify(result,null,2);try{await native({cmd:'status',report:result});}catch{}}
 finally{
  if(tabId!==undefined)try{if(keepTab)await chrome.tabs.update(tabId,{active:true});else if(ownedTab)await chrome.tabs.remove(tabId);}catch{}
  if(keepTab)display.textContent+='\n\nCollection is paused. Complete sign-in or verification in the ChatGPT tab, then start a new test from the extension.';
  await chrome.runtime.sendMessage({kind:'finish',id:runId,status:result});
  document.querySelector('#cancel').disabled=true;
  // Unattended runs close their status tab; results remain in the popup and NAS status file.
  if(scheduled)setTimeout(()=>closeRunner().catch(()=>{}),5000);
 }
}
main().catch(()=>{display.textContent='Collection stopped. Check the extension status.';});
