const same = (a, b) => a && b && (a.key && b.key
  ? a.key === b.key : a.role === b.role && a.text === b.text);
const fuller = (saved, latest) => latest.text.length > saved.text.length ? latest : saved;

// Virtualized windows can omit an unloaded turn inside an otherwise
// overlapping section. Shared message identities anchor an unambiguous merge.
function mergeKeyed(saved, earlier) {
  const savedIndex = new Map(saved.map((row, index) => [row.key, index]));
  const joined = [];
  let left = 0, right = 0, anchors = 0;
  for (let index = 0; index < earlier.length; index++) {
    const match = savedIndex.get(earlier[index].key);
    if (match === undefined) continue;
    if (match < left || (match > left && index > right)) return null;
    joined.push(...saved.slice(left, match), ...earlier.slice(right, index),
      fuller(saved[match], earlier[index]));
    left = match + 1;
    right = index + 1;
    anchors++;
  }
  if (!anchors || (left < saved.length && right < earlier.length)) return null;
  joined.push(...saved.slice(left), ...earlier.slice(right));
  return joined.length === saved.length && joined.every((row, index) => row === saved[index])
    ? saved : joined;
}

export function mergeEarlier(saved, earlier) {
  if (!earlier.length) return null;
  if ([saved, earlier].every(rows => rows.every(row => row.key) &&
      new Set(rows.map(row => row.key)).size === rows.length)) return mergeKeyed(saved, earlier);
  // A scroll step can move within a window that was already collected.
  const start = saved.findIndex((_, index) => earlier.every((turn, step) => same(turn, saved[index + step])));
  if (start >= 0) {
    const expanded = earlier.map((turn, index) => fuller(saved[start + index], turn));
    return expanded.some((turn, index) => turn !== saved[start + index])
      ? [...saved.slice(0, start), ...expanded, ...saved.slice(start + earlier.length)] : saved;
  }
  for (let overlap = Math.min(saved.length, earlier.length); overlap > 0; overlap--) {
    const tail = earlier.slice(-overlap);
    if (tail.every((turn, index) => same(turn, saved[index]))) {
      return [...earlier.slice(0, -overlap),
        ...tail.map((turn, index) => fuller(saved[index], turn)), ...saved.slice(overlap)];
    }
  }
  return null;
}

export async function collectHistory(initial, {read, scroll, pause, visit}) {
  let saved = initial.messages;
  let current = initial;
  let stride = Math.max(200, initial.view.viewport * 2);
  let topChecks = 0;
  let stalled = 0;
  try {
    for (let step = 0; step < 160; step++) {
      const target = Math.max(0, current.view.top - stride);
      await scroll(target);
      await pause(700);
      let next = await read();
      let joined = mergeEarlier(saved, next.messages);
      for (let retry = 0; retry < 4 && (!joined || next.pending || next.streaming); retry++) {
        // A window may still be hydrating, or the scroll step may have crossed a gap.
        await pause(1000);
        next = await read();
        joined = mergeEarlier(saved, next.messages);
      }
      if (!joined || next.pending || next.streaming) {
        const growth = Math.max(0, next.view.height - current.view.height);
        if (!growth && (stride <= 150 || current.view.top <= 2)) {
          return {messages:saved, history:'gap'};
        }
        current = {...current, view:{...next.view,top:current.view.top + growth}};
        stride = Math.max(150, stride / 2);
        continue;
      }
      if (joined.length > 10000 || joined.reduce((sum, turn) => sum + turn.text.length, 0) > 1048576) {
        return {messages:saved, history:'limit'};
      }
      const grew = joined.length > saved.length;
      const unchanged = joined === saved && next.view.height === current.view.height;
      topChecks = next.view.top <= 2 && unchanged ? topChecks + 1 : 0;
      if (topChecks >= 4) return {messages:joined, history:'visible_start'};
      stalled = next.view.top >= current.view.top - 2 && next.view.top > 2 && unchanged ? stalled + 1 : 0;
      if (stalled >= 4) return {messages:joined, history:'stalled'};
      current = next;
      saved = joined;
      if (grew) await visit?.();
    }
  } catch (error) {
    if (error?.message !== 'run_timeout') throw error;
  }
  return {messages:saved, history:'limit'};
}
