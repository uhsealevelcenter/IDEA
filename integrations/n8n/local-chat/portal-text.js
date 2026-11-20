// IDEA emits executable code and console output as Markdown fences, including
// four-backtick fences that can contain ordinary three-backtick examples.
export function splitReply(text, streaming) {
  const blocks = [];
  const fences = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)(?:\r?\n|$)/gm;
  let cursor = 0;
  let open;

  function prose(end) {
    const body = text.slice(cursor, end);
    if (body.trim()) blocks.push({ kind: 'prose', body });
  }

  for (const match of text.matchAll(fences)) {
    const fence = match[1];
    const info = match[2].trim();
    if (open) {
      if (fence[0] !== open.fence[0] || fence.length < open.fence.length || info) continue;
      blocks.push({ ...open.block, body: text.slice(open.start, match.index), streaming: false });
      cursor = match.index + match[0].length;
      open = undefined;
      continue;
    }
    if (fence[0] === '`' && info.includes('`')) continue;
    const language = info.split(/\s+/, 1)[0];
    const kind = language.toLowerCase() === 'output' ? 'output' : 'code';
    prose(match.index);
    open = { fence, start: match.index + match[0].length, block: { kind, language } };
  }

  if (open) blocks.push({ ...open.block, body: text.slice(open.start), streaming });
  else prose(text.length);
  return blocks;
}
