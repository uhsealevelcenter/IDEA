import { splitReply } from './portal-text.js';

const names = {
  idea: {
    title: 'IDEA', subtitle: 'Intelligent Data Exploring Assistant',
    webhook: '892d894b-ff0d-4a86-a8c1-95468df23065',
    suggestions: ['What can IDEA help me with?', 'Plot monthly sea levels for Honolulu', 'What did Mars InSight measure?'],
  },
  sea: {
    title: 'SEA', subtitle: 'Station Explorer Assistant',
    webhook: 'f311b56b-2b73-4ef4-9ae9-2cf80f8945e3',
    suggestions: ['Find tide gauges near Honolulu', 'Plot monthly sea levels for Honolulu'],
  },
};

const assistant = location.pathname === '/SEA-portal' ? 'sea' : 'idea';
const profile = names[assistant];
const root = document.documentElement;
const params = new URLSearchParams(location.search);
const framed = window.parent !== window;
root.dataset.assistant = assistant;
root.classList.toggle('framed', framed);
root.classList.toggle('embed', params.get('embed') === '1' || (framed && params.get('embed') !== 'floating'));

const get = id => document.getElementById(id);
const transcript = get('transcript');
const welcome = get('welcome');
const form = get('chat-form');
const input = get('prompt');
const reset = get('reset');
const stop = get('stop');
const status = get('status');
const send = get('send');

document.title = profile.title;
get('portal-name').textContent = profile.title;
get('portal-subtitle').textContent = profile.subtitle;
get('welcome-name').textContent = profile.title;
get('footer-name').textContent = profile.title;
input.placeholder = `Ask ${profile.title} a question…`;
for (const example of profile.suggestions) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = example;
  button.addEventListener('click', () => {
    input.value = example;
    input.focus();
  });
  get('suggestions').append(button);
}

// Load the optional Markdown renderer without blocking chat. HTML supplied by
// a reply stays disabled. The fallback renders it as text.
let markdown;
import('https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/+esm').then(({ default: MarkdownIt }) => {
  markdown = new MarkdownIt({ html: false, linkify: true });
}).catch(() => {});

let highlighter;
function highlightCode(view) {
  const block = view.block;
  if (!view.node.open || block?.streaming || !block?.language || block.body.length > 30000 ||
      view.highlighted === block.body) return;
  highlighter ??= import('https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.11.1/es/highlight.min.js')
    .then(module => module.default).catch(() => null);
  highlighter.then(syntax => {
    if (view.block !== block || !view.node.open || !syntax?.getLanguage(block.language)) return;
    view.code.innerHTML = syntax.highlight(block.body, { language: block.language, ignoreIllegals: true }).value;
    view.highlighted = block.body;
  });
}

function createSession() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // The next-dev HTTP origin supports getRandomValues without a secure context.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
}

let sessionId = createSession();
let controller;
let shouldScroll = true;

function announce(text, error = false) {
  status.textContent = text;
  status.toggleAttribute('data-error', error);
}

function scrollToLatest() {
  if (shouldScroll) transcript.scrollTop = transcript.scrollHeight;
}
transcript.addEventListener('scroll', () => {
  shouldScroll = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 64;
}, { passive: true });

function addMessage(role, message = '') {
  const element = document.createElement('article');
  element.className = `message ${role}`;
  element.setAttribute('aria-label', role === 'user' ? 'Your message' : `${profile.title} response`);
  element.textContent = message;
  transcript.append(element);
  return element;
}

function createBlock(kind) {
  if (kind === 'prose') {
    const node = document.createElement('div');
    node.className = 'prose';
    return { node, kind };
  }
  const node = document.createElement('details');
  node.className = 'trace';
  const summary = document.createElement('summary');
  const label = document.createElement('span');
  const badge = document.createElement('span');
  badge.className = 'stream-badge';
  badge.textContent = 'Receiving…';
  const pre = document.createElement('pre');
  const code = document.createElement('code');
  summary.append(label, badge);
  pre.append(code);
  node.append(summary, pre);
  const view = { node, kind, label, code };
  if (kind === 'code') node.addEventListener('toggle', () => highlightCode(view));
  return view;
}

function renderProse(element, body) {
  if (!markdown) {
    const text = document.createElement('div');
    text.className = 'plain-text';
    text.textContent = body;
    element.replaceChildren(text);
    return;
  }
  element.innerHTML = markdown.render(body);
  for (const link of element.querySelectorAll('a')) {
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
}

function updateReply(element, views, text, streaming) {
  const blocks = splitReply(text, streaming);
  for (const [index, block] of blocks.entries()) {
    let view = views[index];
    if (view?.kind !== block.kind) {
      const next = createBlock(block.kind);
      if (view) view.node.replaceWith(next.node);
      else element.append(next.node);
      views[index] = view = next;
    }
    if (block.kind === 'prose') {
      if (view.body !== block.body || view.renderer !== markdown) {
        renderProse(view.node, block.body);
        view.renderer = markdown;
      }
    } else {
      view.label.textContent = block.kind === 'output' ? 'Code output' :
        block.language ? `Code · ${block.language}` : 'Code';
      view.node.toggleAttribute('data-streaming', Boolean(block.streaming));
      if (view.body !== block.body) {
        view.code.textContent = block.body;
        view.highlighted = undefined;
      }
      if (block.kind === 'code') {
        view.block = block;
        highlightCode(view);
      }
    }
    view.body = block.body;
  }
  for (const view of views.splice(blocks.length)) view.node.remove();
}

async function* chatFrames(response) {
  if (!response.body) throw new Error('The chat server returned an empty response.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line);
      }
      if (done) {
        if (buffer.trim()) yield JSON.parse(buffer);
        return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question || controller) return;

  const request = new AbortController();
  controller = request;
  welcome.hidden = true;
  send.hidden = true;
  stop.hidden = false;
  input.value = '';
  reset.disabled = true;
  announce(`${profile.title} is working…`);
  addMessage('user', question);
  const reply = addMessage('assistant');
  const views = [];
  let content = '';
  let paint = 0;
  shouldScroll = true;
  scrollToLatest();

  function render(streaming) {
    paint = 0;
    updateReply(reply, views, content, streaming);
    scrollToLatest();
  }

  try {
    const response = await fetch(`/n8n/webhook/${profile.webhook}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'sendMessage', sessionId, chatInput: question }),
      signal: request.signal,
    });
    if (!response.ok) {
      const message = response.status === 429 ? 'Chat is receiving too many requests. Try again shortly.' :
        response.status === 404 ? 'The chat workflow is unavailable. Check that it is published.' :
          `The chat request failed (HTTP ${response.status}). Try again.`;
      throw new Error(message);
    }
    for await (const frame of chatFrames(response)) {
      if (frame.type === 'error' || frame.error) throw new Error('IDEA reported an error while responding. Try again.');
      if (frame.type !== 'item' || typeof frame.content !== 'string') continue;
      content += frame.content;
      if (!paint) paint = requestAnimationFrame(() => render(true));
    }
    if (!content.trim()) throw new Error('IDEA returned an empty reply. Try again.');
    announce('');
  } catch (error) {
    if (request.signal.aborted) announce('Stopped receiving the response.');
    else announce(error.message, true);
    if (!content.trim()) reply.remove();
  } finally {
    if (paint) cancelAnimationFrame(paint);
    render(false);
    controller = undefined;
    reset.disabled = false;
    stop.hidden = true;
    send.hidden = false;
  }
});

input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
stop.addEventListener('click', () => {
  controller?.abort();
  input.focus();
});
reset.addEventListener('click', () => {
  sessionId = createSession();
  transcript.replaceChildren(welcome);
  welcome.hidden = false;
  reset.disabled = true;
  input.value = '';
  input.focus();
  announce('');
});

const panel = document.querySelector('.portal');
const header = document.querySelector('.portal-header');
const compact = matchMedia('(max-width: 39rem), (max-height: 25rem)');
const floating = !root.classList.contains('embed');
let positioned = false;
const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

function axis(position, size, delta, minSize, viewport, startEdge, endEdge) {
  const length = clamp(size, minSize, viewport);
  const start = clamp(position, 0, viewport - length);
  if (startEdge) {
    const edge = clamp(start + delta, 0, start + length - minSize);
    return [edge, start + length - edge];
  }
  if (endEdge) return [start, clamp(length + delta, minSize, viewport - start)];
  return [clamp(start + delta, 0, viewport - length), length];
}

function geometry(rect, dx, dy, edge = '') {
  const west = edge.includes('w');
  const east = edge.includes('e');
  const north = edge.includes('n');
  const south = edge.includes('s');
  const [x, width] = axis(rect.left, rect.width, edge && !west && !east ? 0 : dx,
    Math.min(360, innerWidth), innerWidth, west, east);
  const [y, height] = axis(rect.top, rect.height, edge && !north && !south ? 0 : dy,
    Math.min(380, innerHeight), innerHeight, north, south);
  Object.assign(panel.style, { width: `${width}px`, height: `${height}px`, left: `${x}px`, top: `${y}px`, transform: 'none' });
  positioned = true;
}

function drag(handle, edge = '') {
  let start;
  handle.addEventListener('pointerdown', event => {
    if (!floating || compact.matches || !event.isPrimary || event.button !== 0) return;
    if (handle === header && event.target.closest('button, a')) return;
    event.preventDefault();
    start = { id: event.pointerId, x: event.clientX, y: event.clientY, rect: panel.getBoundingClientRect() };
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', event => {
    if (start?.id !== event.pointerId) return;
    geometry(start.rect, event.clientX - start.x, event.clientY - start.y, edge);
  });
  handle.addEventListener('lostpointercapture', () => { start = undefined; });
}

for (const handle of [header, ...panel.querySelectorAll('.resize-edge')]) {
  const edge = handle.dataset.edge;
  drag(handle, edge);
  handle.addEventListener('keydown', event => {
    const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (!floating || compact.matches || event.target !== handle || !Object.hasOwn(directions, event.key)) return;
    const [x, y] = directions[event.key];
    if (edge && (x ? !/[ew]/.test(edge) : !/[ns]/.test(edge))) return;
    event.preventDefault();
    const step = event.shiftKey ? 32 : 8;
    geometry(panel.getBoundingClientRect(), x * step, y * step, edge);
  });
}
function fitViewport() {
  header.tabIndex = floating && !compact.matches ? 0 : -1;
  if (compact.matches && positioned) {
    panel.removeAttribute('style');
    positioned = false;
  } else if (positioned) {
    geometry(panel.getBoundingClientRect(), 0, 0);
  }
}
window.addEventListener('resize', fitViewport);
compact.addEventListener('change', fitViewport);
fitViewport();
