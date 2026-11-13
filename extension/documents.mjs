import {HOST, ORIGIN} from './core.mjs';

// Runs on the rendered chat page. Only interact with file controls inside chat turns.
export function documentCards(path, action, key, name) {
  const chat = /\/c\/([A-Za-z0-9-]+)$/;
  const expected = path.match(chat)?.[1];
  if (location.origin !== 'https://chatgpt.com' || !expected ||
      location.pathname.match(chat)?.[1] !== expected) return null;
  const close = () => document.querySelector('button[aria-label="Close viewer"]')?.click();
  if (action === 'close') { close(); return true; }
  const rendered=node=>node.getClientRects().length && !node.closest('[inert],[aria-hidden="true"]');
  const outputsHeading = [...document.querySelectorAll('button[aria-expanded]')].find(button =>
    rendered(button) && (button.getAttribute('aria-label') || button.textContent).trim() === 'Outputs');
  if (action === 'show-outputs') {
    if (!outputsHeading) {
      const toggle = [...document.querySelectorAll('button[aria-label="Toggle pinned summary"],button[aria-label="Toggle summary"]')]
        .find(button => button.getClientRects().length && !button.closest('[inert]'));
      if (toggle && toggle.getAttribute('aria-pressed') !== 'true') toggle.click();
      return !!toggle;
    }
    if (outputsHeading.getAttribute('aria-expanded') === 'false') outputsHeading.click();
    return true;
  }
  let outputs;
  for (let parent = outputsHeading?.parentElement; parent; parent = parent.parentElement) {
    if (parent.querySelector('[data-conversation-role],[data-message-author-role]')) break;
    if (parent.querySelector('button[aria-label^="Open "],button[aria-label^="Download "]')) {
      outputs = parent;
      break;
    }
  }
  if (action === 'expand-outputs') {
    const more = [...outputs?.querySelectorAll('button') || []].find(button =>
      /^(show more|show all)(?:\s|$)/i.test(button.textContent.trim()));
    if (more) more.click();
    return !!more;
  }
  const cards = new Map();
  const generatedPrefix = 'Open preview of ';
  const roles = document.querySelectorAll('[data-conversation-role],[data-message-author-role]');
  for (const marker of roles) {
    if (!rendered(marker)) continue;
    const body = marker.matches('h1,h2,h3,h4,h5,h6,[role="heading"]') ? marker.nextElementSibling : marker;
    if (!body) continue;
    const role = marker.getAttribute('data-conversation-role') || marker.getAttribute('data-message-author-role');
    if (role !== 'user' && role !== 'assistant') continue;
    const generated = role === 'assistant';
    const selector = generated ? 'button[aria-label^="Open preview of "]' :
      'button[class~="peer/resource-card"][aria-label]';
    const occurrences = new Map();
    for (const button of body.querySelectorAll(selector)) {
      const label = button.getAttribute('aria-label');
      const filename = generated ? label.slice(generatedPrefix.length) : label;
      if (!/^[^/\\\x00-\x1f\x7f]{1,191}$/.test(filename) ||
          !filename.trim() || filename === '.' || filename === '..') continue;
      const unit = button.closest('[data-chatgpt-search-message-ids]');
      const turn = unit?.getAttribute('data-chatgpt-search-message-ids') ||
        marker.closest('[data-turn-key]')?.getAttribute('data-turn-key');
      if (!generated && (!turn || turn.length > 200)) continue;
      const keyTurn = turn?.slice(0, 200) || 'generated';
      const base = JSON.stringify([keyTurn, filename]);
      const occurrence = occurrences.get(base) || 0;
      occurrences.set(base, occurrence + 1);
      const id = generated ? ['generated', keyTurn, filename, occurrence] : [keyTurn, filename, occurrence];
      cards.set(JSON.stringify(id), {filename, button, generated});
    }
  }
  const outputCards = new Map();
  for (const button of outputs?.querySelectorAll('button[aria-label]') || []) {
    const match = /^(Open|Download) (.+)$/.exec(button.getAttribute('aria-label'));
    if (!match || !/^[^/\\\x00-\x1f\x7f]{1,191}$/.test(match[2]) || !match[2].trim() || ['.', '..'].includes(match[2])) continue;
    const [, operation, filename] = match;
    outputCards.set(JSON.stringify(['output', filename]), {filename, button, generated:true, direct:operation==='Download'});
  }
  const directFor = card => {
    if (!card?.generated) return null;
    for (let parent=card.button.parentElement, depth=0; parent && depth<4; parent=parent.parentElement, depth++) {
      const previews=parent.querySelectorAll('button[aria-label^="Open preview of "]');
      if (previews.length!==1) break;
      const downloads=[...parent.querySelectorAll('button[aria-label="Download file"]')]
        .filter(button=>rendered(button) && !button.disabled);
      if (downloads.length===1) return downloads[0];
    }
    return null;
  };
  if (action === 'output-list') return [...outputCards].map(([id, card]) => [id, card.filename]);
  if (action === 'output-open') {
    const card = outputCards.get(key);
    if (!card || card.filename !== name || card.button.disabled) return false;
    const inline=[...cards.values()].filter(item=>item.generated && item.filename===name);
    const direct=inline.length===1 ? directFor(inline[0]) : null;
    if (direct){direct.click();return 'direct';}
    card.button.click();
    return card.direct ? 'direct' : 'viewer';
  }
  if (action === 'list') return [...cards].map(([id, card]) => [id, card.filename]);
  if (action === 'open') {
    const card = cards.get(key);
    if (!card || card.filename !== name || card.button.disabled) return false;
    const direct=directFor(card);
    if (direct){direct.click();return 'direct';}
    card.button.click();
    return true;
  }
  if (action === 'download') {
    const dot = name.lastIndexOf('.');
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const viewer = [...document.querySelectorAll('button[aria-haspopup="menu"]')].find(button =>
      rendered(button) && ['File actions for ' + stem, 'File actions for ' + name].includes(button.getAttribute('aria-label')));
    const buttons = [...document.querySelectorAll('button[aria-label="Download"]')].filter(rendered);
    const generated = cards.get(key)?.generated || key?.startsWith('["output",');
    const viewerOpen = [...document.querySelectorAll('button[aria-label="Close viewer"]')].some(rendered);
    if (viewer && !buttons.length) {
      if (viewer.getAttribute('aria-expanded') !== 'true') {
        viewer.click();
        return false;
      }
      const menuId = viewer.getAttribute('aria-controls');
      const menu = menuId && document.getElementById(menuId);
      const entries = [...menu?.querySelectorAll('[role="menuitem"]') || []].filter(item =>
        rendered(item) && item.getAttribute('aria-disabled') !== 'true' &&
        /^download(?: file)?$/i.test((item.getAttribute('aria-label') || item.textContent).trim()));
      if (entries.length === 1) {
        entries[0].click();
        return true;
      }
    }
    if ((!viewer && !(generated && viewerOpen)) || buttons.length !== 1 ||
        buttons[0].disabled || buttons[0].getAttribute('aria-busy') === 'true') return false;
    buttons[0].click();
    return true;
  }
  return false;
}

function matchesDownload(item, filename, path) {
  if (item.byExtensionId || !item.filename.startsWith('/chat-files/')) return false;
  const name = item.filename.slice('/chat-files/'.length);
  const matchesName = candidate => {
    const dot = candidate.lastIndexOf('.') > 0 ? candidate.lastIndexOf('.') : candidate.length;
    const extension = candidate.slice(dot);
    const stem = candidate.slice(0, dot);
    return name === candidate || (name.startsWith(stem + ' (') &&
      name.endsWith(')' + extension) &&
      /^\d{1,6}$/.test(name.slice(stem.length + 2, -extension.length - 1)));
  };
  if (!matchesName(filename) && !matchesName(filename.replace(/[<>:"|?*]/g, '_'))) return false;
  try {
    const source = new URL(item.url);
    const referrer = new URL(item.referrer);
    return source.origin === ORIGIN && referrer.origin === ORIGIN &&
      (referrer.pathname === path || referrer.pathname === '/' ||
       referrer.pathname.endsWith(path.slice(path.lastIndexOf('/c/'))));
  } catch {
    return false;
  }
}

export async function collectDocuments({tabId, path, id, title, saved, seen, attempted=new Set(), deferred=new Set(), report, budget, check, includeOutputs=false,maxAttempts=Infinity,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}) {
  const page = async (action, key, filename) => {
    const [item] = await chrome.scripting.executeScript({
      target: {tabId}, func: documentCards, args: [path, action, key ?? null, filename ?? null]
    });
    return item?.result;
  };
  const pause = () => sleep(750);
  const openOutputs = async () => {
    if (!(await page('output-list'))?.length) {
      if (!await page('show-outputs')) return [];
      await pause();
    }
    for (let step=0; step<6 && await page('expand-outputs'); step++) await pause();
    return await page('output-list') || [];
  };
  const outputItems = includeOutputs ? await openOutputs() : [];
  const outputNames = new Set(outputItems.map(([, filename]) => filename));
  const inline = (await page('list') || []).filter(([, filename]) => !outputNames.has(filename));
  // Give unseen files a chance before repeatedly retrying an unavailable one.
  const order = new Map([...attempted].map((key, index) => [key,index]));
  const candidates = [...outputItems, ...inline].sort(([left],[right]) =>
    (order.get(left) ?? -1) - (order.get(right) ?? -1));
  let attempts=0;
  for (const [key, filename] of candidates) {
    if (seen.has(key)) continue;
    if (!saved.has(key) && (budget.left <= 0 || attempts >= maxAttempts)) {
      if (!deferred.has(key)) { deferred.add(key); report.deferred++; }
      continue;
    }
    if (deferred.delete(key)) report.deferred--;
    seen.add(key);
    if (saved.has(key)) { report.already_saved++; continue; }
    budget.left--;
    attempts++;
    await check();
    attempted.delete(key);
    attempted.add(key);
    let downloadId, opened = false, failure = 'viewer_unavailable';
    try {
      if (!chrome.downloads) throw Error('downloads_unavailable');
      const output = key.startsWith('["output",');
      if (output) await openOutputs();
      let startedAfter = new Date().toISOString();
      const result = await page(output ? 'output-open' : 'open', key, filename);
      opened = !!result && result !== 'direct';
      if (!result) throw Error('document_viewer_unavailable');
      failure = 'download_control_unavailable';
      let clicked = result === 'direct';
      for (let attempt = 0; attempt < 12 && !clicked; attempt++) {
        await pause();
        startedAfter = new Date().toISOString();
        clicked = await page('download', key, filename);
      }
      if (!clicked) throw Error('document_download_unavailable');
      failure = 'download_not_seen';
      const startDeadline = Date.now() + 30_000;
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        await check();
        await pause();
        const items = await chrome.downloads.search({startedAfter, limit: 24});
        const matches = items.filter(item => matchesDownload(item, filename, path));
        if (matches.length > 1) { failure = 'ambiguous_download'; break; }
        const [item] = matches;
        if (!item) {
          if (!downloadId && Date.now() >= startDeadline) break;
          continue;
        }
        downloadId = item.id;
        failure = 'download_incomplete';
        if (item.state === 'interrupted') { failure = 'download_interrupted'; break; }
        if (item.bytesReceived > 256 * 1024**2) { failure = 'too_large'; break; }
        if (item.state !== 'complete') continue;
        if (!item.exists || !['safe', 'deepScannedSafe', 'allowlistedByPolicy'].includes(item.danger)) {
          failure = 'download_unavailable';
          break;
        }
        failure = 'writer_rejected';
        const reply = await chrome.runtime.sendNativeMessage(HOST, {
          cmd: 'stage_document', id, title, sourceName: item.filename.slice('/chat-files/'.length),
          filename, bytes: item.fileSize
        });
        if (reply?.ok) {
          saved.add(key);
          report[reply.saved ? 'saved' : 'already_saved']++;
        }
        break;
      }
    } catch (error) {
      if (['run_cancelled', 'run_timeout'].includes(error?.message)) throw error;
      // The transcript can still be saved, and the document can be retried.
    } finally {
      if (opened) await page('close').catch(() => {});
      if (!saved.has(key)) {
        report.skipped++;
        await chrome.runtime.sendNativeMessage(HOST, {
          cmd: 'document_issue', id, filename, failure
        }).catch(() => {});
        report.skipReasons ??= {};
        report.skipReasons[failure] = (report.skipReasons[failure] || 0) + 1;
        if (downloadId !== undefined) await chrome.downloads.cancel(downloadId).catch(() => {});
      }
    }
  }
}
