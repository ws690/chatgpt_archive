import {HOST} from './core.mjs';
try{document.querySelector('#status').textContent=JSON.stringify(await chrome.runtime.sendNativeMessage(HOST,{cmd:'ping'}));}
catch(e){document.querySelector('#status').textContent='Local writer connection: '+String(e.message).slice(0,200);}
