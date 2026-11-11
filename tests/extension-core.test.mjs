import test from 'node:test';
import assert from 'node:assert/strict';
import {validChatPath,chatId,safeError} from '../extension/core.mjs';
import {collectHistory,mergeEarlier} from '../extension/history.mjs';
import {chatFingerprint,isUnchanged,transcriptFingerprint} from '../extension/checkpoints.mjs';
test('chat IDs cannot become arbitrary paths',()=>{assert.equal(validChatPath('/c/abc-123'),true);for(const s of ['/c/../../account','https://evil.example/c/abc','/auth/login','/c/a/b'])assert.equal(validChatPath(s),false);});
test('private exception details are not status text',()=>{assert.equal(safeError(Error('secret session value')),'page_or_browser_error');assert.equal(safeError(Error('authentication_challenge')),'authentication_challenge');});
test('worker honours the daily pause and serializes competing runs',async()=>{
 const savedChrome=globalThis.chrome;
 const store={},listeners=[];
 globalThis.chrome={runtime:{id:'fixture',getURL:p=>'chrome-extension://fixture/'+p,onMessage:{addListener:fn=>listeners.push(fn)}},
  storage:{local:{get:async keys=>Object.fromEntries((typeof keys==='string'?[keys]:keys).map(k=>[k,store[k]])),
   set:async data=>Object.assign(store,data),remove:async key=>{delete store[key];}}}};
 const call=(m,mode='daily')=>new Promise(resolve=>listeners[0](m,
  {id:'fixture',url:'chrome-extension://fixture/runner.html?mode='+mode,tab:{id:1}},resolve));
 try{
  await import('../extension/worker.mjs?test='+Math.random());
  assert.equal((await call({kind:'claim'})).error,'daily_paused');
  await call({kind:'enable',enabled:true});
  const first=await call({kind:'claim'});assert.equal(first.ok,true);
  assert.equal((await call({kind:'claim'},'manual')).error,'another_run_active');
  await call({kind:'finish',id:first.run.id,status:{status:'up_to_date'}});
  assert.equal(store.run,undefined);
 }finally{globalThis.chrome=savedChrome;}
});

test('project conversation paths retain the original conversation ID',()=>{assert.equal(validChatPath('/g/g-p-project/c/abc-123'),true);assert.equal(chatId('/g/g-p-project/c/abc-123'),'abc-123');assert.throws(()=>chatId('/g/../../c/abc'));});

test('chat windows join in order and keep repeated messages',()=>{
 const make=text=>({role:'user',text});
 const later=['two','three','two'].map(make);
 assert.deepEqual(mergeEarlier(later,['one','two','three'].map(make)),
   ['one','two','three','two'].map(make));
 assert.equal(mergeEarlier(later,['unrelated'].map(make)),null);
});

test('history narrows scroll steps if a window would leave a gap',async()=>{
 let top=2000;
 const view=()=>({top,height:2400,viewport:400});
 const make=text=>({role:'user',text});
 const result=await collectHistory({view:view(),messages:['third','fourth'].map(make)},{
  scroll:async offset=>{top=offset;},
  pause:async()=>{},
  read:async()=>({view:view(),messages:(top>=1550?['third','fourth']:
    top>=1350?['second','third']:['first','second']).map(make)})
 });
 assert.equal(result.history,'visible_start');
 assert.deepEqual(result.messages.map(message=>message.text),['first','second','third','fourth']);
});

test('unchanged chats can skip history until the weekly recheck',async()=>{
 const messages=Array.from({length:8},(_,i)=>({role:'user',key:String(i),text:String(i)}));
 const fingerprint=await chatFingerprint('Kitchen',{messages,imageCount:0});
 const previous={fingerprint,checkedAt:0};
 assert.equal(isUnchanged(previous,fingerprint,1000),true);
 assert.equal(isUnchanged(previous,fingerprint,7*24*60*60*1000),false);
 assert.notEqual(await chatFingerprint('Kitchen',{messages,imageCount:1}),fingerprint);
 const edited=[{...messages[0],text:'earlier edit'},...messages.slice(1)];
 assert.equal(await chatFingerprint('Kitchen',{messages:edited,imageCount:0}),fingerprint);
 assert.notEqual(await transcriptFingerprint('Kitchen',edited),await transcriptFingerprint('Kitchen',messages));
});

test('history traverses long overlapping windows without skipping turns',async()=>{
 const turns=Array.from({length:1000},(_,index)=>({role:'user',key:String(index),text:String(index)}));
 let top=149000;
 const sample=()=>({
   view:{top,height:149100,viewport:100},
   messages:turns.slice(Math.floor(top/150),Math.floor(top/150)+9)
 });
 const result=await collectHistory(sample(),{
   scroll:async offset=>{top=offset;},pause:async()=>{},read:async()=>sample()
 });
 assert.equal(result.history,'visible_start');
 assert.deepEqual(result.messages,turns);
});
