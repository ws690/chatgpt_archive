import {HOST} from './core.mjs';

// Runs inside the chat document so it can read that document's image blobs.
async function readChatImage(path, source) {
  if (location.origin !== 'https://chatgpt.com' || location.pathname !== path) return null;
  const markers = document.querySelectorAll(
    '[data-conversation-role="user"],[data-conversation-role="assistant"],' +
    '[data-message-author-role="user"],[data-message-author-role="assistant"]'
  );
  const bodies = [...markers].map(marker => marker.matches('h1,h2,h3,h4,h5,h6,[role="heading"]')
    ? marker.nextElementSibling : marker).filter(Boolean);
  const images = [...new Set(bodies.flatMap(body => [...body.querySelectorAll('img')])
    .map(image => image.currentSrc || image.src))]
    .filter(url => url.startsWith('blob:https://chatgpt.com/'));
  if (source === undefined) return images;
  if (!images.includes(source)) return null;
  const blob = await (await fetch(source)).blob();
  if (blob.size === 0 || blob.size > 8 * 1024**2) return null;
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',', 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export async function saveChatImages({tabId, path, id, title, remaining, check, seen=new Set()}) {
  const read = async source => {
    const [result] = await chrome.scripting.executeScript({
      target: {tabId}, func: readChatImage, args: source === undefined ? [path] : [path, source]
    });
    return result?.result;
  };
  const report = {saved: 0, already_saved: 0, skipped: 0};
  const sources = (await read() || []).filter(source => !seen.has(source));
  for (const source of sources.slice(0, remaining)) {
    await check();
    seen.add(source);
    try {
      const data = await read(source);
      const response = data && await chrome.runtime.sendNativeMessage(HOST, {
        cmd: 'save_image', id, title, index:seen.size, data
      });
      if (!response?.ok) report.skipped++;
      else if (response.saved) report.saved++;
      else report.already_saved++;
    } catch {
      report.skipped++;
    }
  }
  report.skipped += Math.max(0, sources.length - remaining);
  return report;
}
