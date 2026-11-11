const WEEK = 7 * 24 * 60 * 60 * 1000;

async function fingerprint(value) {
  const input = new TextEncoder().encode(JSON.stringify(value));
  const hash = await crypto.subtle.digest('SHA-256', input);
  return [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function chatFingerprint(title, sample) {
  const tail = sample.messages.slice(-6).map(({role, key, text}) => [role, key, text]);
  return fingerprint([title, sample.imageCount, tail]);
}

export function transcriptFingerprint(title, messages) {
  return fingerprint([title, messages.map(({role, text}) => [role, text])]);
}

export function isUnchanged(previous, fingerprint, now = Date.now()) {
  return previous?.fingerprint === fingerprint && previous.checkedAt <= now &&
    now - previous.checkedAt < WEEK;
}

// Resume discovery after the last attempted chat, including after an interrupted run.
export function rotateChats(entries, after, limit, identify) {
  const next = entries.findIndex(([path]) => identify(path) === after) + 1;
  return [...entries.slice(next), ...entries.slice(0, next)].slice(0, limit);
}
