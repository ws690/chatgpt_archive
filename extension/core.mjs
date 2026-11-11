export const HOST = 'local.nas_chat_archive';
export const ORIGIN = 'https://chatgpt.com';
export function validChatPath(path) {return /^\/(?:g\/[A-Za-z0-9_-]+\/)?c\/[A-Za-z0-9-]{1,100}$/.test(path);}
export function chatId(path) {if(!validChatPath(path))throw Error('page_or_browser_error');return path.slice(path.lastIndexOf('/c/')+3);}
export function safeError(e) {
  const codes=['authentication_required','authentication_challenge','chatgpt_unreachable','route_error','no_visible_chats','no_messages','conversation_not_stable','oversized_conversation','run_cancelled','run_timeout','local_writer_unavailable','page_or_browser_error','native_publish_failed','another_run_active'];
  return codes.includes(e?.message)?e.message:'page_or_browser_error';
}
// Serialized into an isolated extension world. Do not add closure dependencies.
export function inspectPage(mode, offset) {
  if(location.origin!=='https://chatgpt.com')return {error:'authentication_required'};
  const title=document.title.toLowerCase();
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>1 && r.height>1 && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)!==0;};
  const titleChallenge=['just a moment','verify you are human','access denied'].some(x=>title.includes(x));
  const visibleChecks=[...document.querySelectorAll('iframe[src*="challenges.cloudflare.com"], input[type="password"]')].filter(visible).length;
  if(titleChallenge || visibleChecks)return {error:'authentication_challenge',checks:{verification_title:titleChallenge,visible_check_elements:visibleChecks}};
  if(/\/auth\/|\/log-in/.test(location.pathname))return {error:'authentication_required'};
  // Docked and floating chats may both exist in the DOM. Read the rendered one.
  const rendered=node=>node.getClientRects().length && !node.closest('[inert],[aria-hidden="true"]');
  const oldNodes = [...document.querySelectorAll('[data-message-author-role="user"], [data-message-author-role="assistant"]')].filter(rendered);
  const roleAttribute = oldNodes.length ? 'data-message-author-role' : 'data-conversation-role';
  const nodes = oldNodes.length ? oldNodes : [...document.querySelectorAll('[data-conversation-role="user"], [data-conversation-role="assistant"]')].filter(rendered);
  if(!nodes.length && (document.body?.innerText||'').includes('Route Error (400 Invalid content type'))return {error:'route_error'};
  if(mode==='messages' || mode==='history-scroll') {
    let scrollRoot=(nodes.find(node=>node.getAttribute(roleAttribute)==='user') || nodes[0])?.parentElement;
    for(;scrollRoot;scrollRoot=scrollRoot.parentElement) {
      if(scrollRoot.scrollHeight>scrollRoot.clientHeight+10 &&
         /auto|scroll/.test(getComputedStyle(scrollRoot).overflowY))break;
    }
    scrollRoot ||= document.scrollingElement;
    if(mode==='history-scroll'){
      scrollRoot?.scrollTo({top:offset,behavior:'instant'});
      return {};
    }
    const turns = [...nodes].map(node => {
      const role = node.getAttribute(roleAttribute);
      // The new layout puts the role on an accessibility heading before the body.
      const body = node.matches('h1,h2,h3,h4,h5,h6,[role="heading"]')
        ? node.nextElementSibling : node;
      const text = body && (body === node || !body.hasAttribute(roleAttribute))
        ? body.innerText?.trim() : '';
      const messageKey=body?.getAttribute('data-chatgpt-search-message-ids') ||
        body?.getAttribute('data-message-id') ||
        body?.querySelector('[data-chatgpt-selection-message-id]')?.getAttribute('data-chatgpt-selection-message-id');
      const turnKey=messageKey || node.closest('[data-turn-key]')?.getAttribute('data-turn-key');
      const key=turnKey && turnKey.length<=200 ? role+':'+turnKey : undefined;
      const images=body?.querySelectorAll('img').length || 0;
      return {role, key, images, text:text || (images ? '[Image attachment]' : '')};
    });
    const messages = turns.filter(({text}) => text && !/^(you|chatgpt) said:?$/i.test(text));
    if(messages.some(m=>m.text.length>262144) || messages.length>10000 || messages.reduce((a,m)=>a+m.text.length,0)>1048576)return {error:'oversized_conversation'};
    const streaming = [...document.querySelectorAll('button')].some(button =>
      visible(button) && /stop (generating|streaming)/i.test(button.getAttribute('aria-label') || ''));
    const view={top:Math.max(0,scrollRoot?.scrollTop||0), height:scrollRoot?.scrollHeight||0,
      viewport:scrollRoot?.clientHeight||0};
    const pending=turns.some(turn=>turn.role==='assistant' && !turn.text);
    const imageCount=turns.reduce((count,turn)=>count+turn.images,0);
    return {messages,streaming,view,pending,imageCount,assistantExpected:turns.some(({role})=>role==='assistant')};
  }
  if(mode==='prepare'){
    const button=document.querySelector('button[data-testid="open-sidebar-button"]') || [...document.querySelectorAll('button')].find(b=>/^open sidebar$/i.test(b.getAttribute('aria-label')||''));
    if(button && visible(button))button.click();
  }
  const chatPath=/^\/(?:g\/[A-Za-z0-9_-]+\/)?c\/[A-Za-z0-9-]{1,100}$/;
  const all=[...document.querySelectorAll('a[href]')].filter(a=>{try{const u=new URL(a.href,location.origin);return u.origin===location.origin && chatPath.test(u.pathname);}catch{return false;}});
  if(!all.length && [...document.querySelectorAll('button,a')].some(e=>visible(e)&&/^(log in|sign in)$/i.test(e.innerText.trim())))return {error:'authentication_required'};
  if(mode==='project-links'){
    const project=location.pathname.match(/^\/g\/(g-p-[A-Za-z0-9_-]+)\/project\/?$/);
    const projectChats=project ? all.filter(a=>{
      const link=new URL(a.href).pathname;
      const slug=link.split('/')[2];
      return rendered(a) && a.closest('main') && !a.closest('nav,aside') &&
        (link.startsWith('/c/') || slug===project[1] || slug?.startsWith(project[1]+'-'));
    }) : [];
    return {links:projectChats.map(a=>({path:new URL(a.href).pathname,
      title:a.innerText.trim().slice(0,1024)||'Untitled'})).slice(0,200)};
  }
  const projects=[...document.querySelectorAll('nav a[href],aside a[href],header a[href]')].map(a=>new URL(a.href))
    .filter(u=>u.origin===location.origin && /^\/g\/g-p-[A-Za-z0-9_-]+\/project\/?$/.test(u.pathname))
    .map(u=>u.pathname);
  const scoped=all.filter(a=>a.closest('nav,aside,#history,[data-testid="history"]'));
  const links=(scoped.length?scoped:all).slice(0,200).map(a=>({path:new URL(a.href,location.origin).pathname,title:a.innerText.trim().slice(0,1024)||'Untitled'})).filter(a=>chatPath.test(a.path));
  if(mode==='scroll') {
    const roots=[...document.querySelectorAll('nav,aside,#history,[data-testid="history"]')];
    for(const a of all.slice(0,5)){let e=a;for(let i=0;e && i<10;i++,e=e.parentElement)roots.push(e);}
    for(const root of new Set(roots))for(const e of [root,...root.querySelectorAll('*')])if(e.scrollHeight>e.clientHeight+10 && /auto|scroll/.test(getComputedStyle(e).overflowY))e.scrollTop+=Math.max(400,e.clientHeight);
  }
  return {links,projects:[...new Set(projects)],scope:scoped.length?'sidebar':'visible_chat_links',path:location.pathname};
}
// Counts and attribute names only: no chat text, titles, IDs, URLs or cookies.
export function inspectLayout(){
 const selectors={author_nodes:'[data-message-author-role]',user_role_nodes:'[data-message-author-role="user"]',assistant_role_nodes:'[data-message-author-role="assistant"]',turn_nodes:'[data-turn]',conversation_turns:'[data-testid^="conversation-turn"]',articles:'article',role_articles:'[role="article"]',message_ids:'[data-message-id]',markdown_nodes:'.markdown',user_testids:'[data-testid="user-message"]',assistant_testids:'[data-testid="assistant-message"]',main_nodes:'main,[role="main"]',iframes:'iframe'};
 const counts=Object.fromEntries(Object.entries(selectors).map(([k,s])=>[k,document.querySelectorAll(s).length]));
 const attributes={},author_attributes={};let shadows=0;
 for(const e of [...document.querySelectorAll('body *')].slice(0,20000)){
  if(e.shadowRoot)shadows++;
  for(const a of e.attributes){
   if(/^data-[a-z-]{1,50}$/.test(a.name) && /message|turn|author|role|content/.test(a.name)){
    attributes[a.name]=(attributes[a.name]||0)+1;
    if(a.value==='user'||a.value==='assistant'){
     author_attributes[a.name]??={user:0,assistant:0};author_attributes[a.name][a.value]++;
    }
   }
  }
 }
 const stops=[...document.querySelectorAll('button')].filter(b=>/stop (generating|streaming)/i.test(b.getAttribute('aria-label')||''));
 counts.streaming_buttons=stops.length;
 counts.visible_streaming_buttons=stops.filter(b=>{const r=b.getBoundingClientRect(),s=getComputedStyle(b);return r.width>1 && r.height>1 && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)!==0;}).length;
 counts.shadow_roots=shadows;
 counts.you_headings=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(e=>/^you said:?$/i.test(e.textContent.trim())).length;
 counts.assistant_headings=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(e=>/^chatgpt said:?$/i.test(e.textContent.trim())).length;
 return {counts,attributes,author_attributes};
}
