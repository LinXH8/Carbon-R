/* Carbon R - the page: a conversation, with documents attached when there are any.
 *
 * A question can be asked on its own (methodology, definitions) or with documents. Files
 * wait in the composer and are uploaded only when the question is sent, so a file removed
 * before sending never enters the session - the server reads every document a session
 * holds, and a document the user took back must not be one of them.
 *
 * The answer is drawn by render.js from the structured `figures` the API returns, never
 * from the agent's prose; nothing here parses a number out of `reply`. Formatting is
 * digit grouping only, done in render.js. There is no arithmetic in this file.
 */

import { h, stepNode, notice, renderAnswer, renderActivity, done as didTool } from '/static/render.js';

const $ = (id) => document.getElementById(id);

const thread = $('thread');
const composer = $('composer');
const question = $('question');
const fileInput = $('file');
const attachButton = $('attach');
const attachmentList = $('attachments');
const sendButton = $('send');
const hint = $('composer-hint');
const newChat = $('new-chat');
const dropOverlay = $('drop-overlay');

// Mirrors api/uploads.py, so a file the server would refuse is refused on attaching it
// rather than after the question has been sent. The server still checks.
const ALLOWED = ['.pdf', '.png', '.jpg', '.jpeg'];
const MAX_BYTES = 10 * 1024 * 1024;
const HINT = hint.textContent;

// Asked when documents are sent with no question of their own.
const DEFAULT_QUESTION = 'What are the emissions from this document? Show the factor you used and where it comes from.';

const state = { sessionId: null, pending: [], busy: false };

/* --- attaching ------------------------------------------------------------------------ */

const suffixOf = (name) => (name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '');

function attach(files) {
  const refused = [];
  for (const file of files) {
    if (!ALLOWED.includes(suffixOf(file.name))) refused.push(`${file.name} is not a PDF, PNG or JPEG`);
    else if (file.size > MAX_BYTES) refused.push(`${file.name} is over the 10 MB limit`);
    else state.pending.push(file);
  }
  setHint(refused.length ? `${refused.join('; ')}.` : null);
  renderPending();
  question.focus();
}

function setHint(problem) {
  hint.textContent = problem || HINT;
  if (problem) hint.setAttribute('data-kind', 'bad');
  else hint.removeAttribute('data-kind');
}

function chip(name, extra) {
  return h('li', { class: 'chip' }, [
    h('span', { class: 'chip-kind', text: suffixOf(name).slice(1).toUpperCase() || 'FILE' }),
    h('span', { class: 'chip-name', text: name, title: name }),
    extra,
  ]);
}

function renderPending() {
  attachmentList.className = 'chips';
  attachmentList.replaceChildren(...state.pending.map((file) => {
    const remove = h('button', { type: 'button', class: 'chip-remove', 'aria-label': `Remove ${file.name}`, text: '×' });
    remove.addEventListener('click', () => {
      state.pending = state.pending.filter((f) => f !== file);
      renderPending();
      question.focus();
    });
    return chip(file.name, remove);
  }));
  updateSend();
}

attachButton.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  attach([...fileInput.files]);
  fileInput.value = '';
});

question.addEventListener('paste', (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); attach(files); }
});

// Drop anywhere on the page. The counter keeps the overlay up while the pointer crosses
// child elements, each of which fires its own dragenter and dragleave.
let dragDepth = 0;
const carriesFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragDepth += 1;
  dropOverlay.hidden = false;
});
window.addEventListener('dragover', (e) => { if (carriesFiles(e)) e.preventDefault(); });
window.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropOverlay.hidden = true;
});
window.addEventListener('drop', (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  dropOverlay.hidden = true;
  attach([...e.dataTransfer.files]);
});

/* --- the composer --------------------------------------------------------------------- */

function autosize() {
  question.style.height = 'auto';
  question.style.height = `${question.scrollHeight}px`;
}

function updateSend() {
  sendButton.disabled = state.busy || (!question.value.trim() && !state.pending.length);
}

question.addEventListener('input', () => { autosize(); updateSend(); });
question.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    composer.requestSubmit();
  }
});
composer.addEventListener('submit', (e) => { e.preventDefault(); send(); });

for (const button of document.querySelectorAll('.suggestion')) {
  button.addEventListener('click', () => {
    question.value = button.dataset.prompt;
    autosize();
    updateSend();
    if (button.hasAttribute('data-attach')) fileInput.click();
    else question.focus();
  });
}

newChat.addEventListener('click', () => {
  if (state.busy) return;
  state.sessionId = null;
  state.pending = [];
  for (const turn of thread.querySelectorAll('.turn')) turn.remove();
  document.body.setAttribute('data-empty', '');
  newChat.hidden = true;
  question.value = '';
  autosize();
  renderPending();
  setHint(null);
  question.focus();
});

function setBusy(busy) {
  state.busy = busy;
  if (busy) sendButton.setAttribute('data-busy', '');
  else sendButton.removeAttribute('data-busy');
  sendButton.setAttribute('aria-label', busy ? 'Working' : 'Send');
  updateSend();
}

/* --- a turn --------------------------------------------------------------------------- */

function scrollToEnd() {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
}

async function send() {
  const files = state.pending;
  const text = question.value.trim() || (files.length ? DEFAULT_QUESTION : '');
  if (state.busy || !text) return;

  document.body.removeAttribute('data-empty');
  newChat.hidden = false;
  state.pending = [];
  question.value = '';
  autosize();
  renderPending();
  setHint(null);
  setBusy(true);

  const fileChips = h('ul', { class: 'chips', 'aria-label': 'Attached' }, files.map((file) => chip(file.name)));
  const activity = activityPanel();
  const answer = h('div', { class: 'answer' });
  thread.append(h('article', { class: 'turn' }, [
    h('div', { class: 'user-msg' }, [files.length ? fileChips : null, h('p', { class: 'user-text', text })]),
    h('div', { class: 'assistant' }, [activity.node, answer]),
  ]));
  scrollToEnd();

  try {
    for (const [index, file] of files.entries()) {
      activity.label.textContent = `Uploading ${file.name}…`;
      const uploaded = await upload(file, answer);
      if (!uploaded) { activity.node.remove(); return; }
      // The id the agent will name the document by, shown beside its name.
      fileChips.children[index].append(h('code', { class: 'chip-id', text: uploaded.document_id }));
    }
    await ask(text, activity, answer);
  } finally {
    setBusy(false);
    question.focus();
  }
}

async function upload(file, answer) {
  const body = new FormData();
  body.append('file', file);
  if (state.sessionId) body.append('session_id', state.sessionId);
  try {
    const response = await fetch('/upload', { method: 'POST', body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      answer.replaceChildren(notice('bad', `${file.name} was not accepted`,
        `${payload.detail || response.statusText} The question was not sent.`));
      return null;
    }
    state.sessionId = payload.session_id;
    return payload;
  } catch (error) {
    answer.replaceChildren(notice('bad', `${file.name} did not reach the server`,
      `${String(error)} The question was not sent.`));
    return null;
  }
}

/* The working behind an answer, closed by default like a chat's "thinking" row: the
   summary names the step in progress, then what was done, and carries the count of audit
   flags so a failed audit shows even while the panel stays shut. */
function activityPanel() {
  const label = h('span', { class: 'activity-label', text: 'Thinking…' });
  const flags = h('span', { class: 'activity-flags', hidden: true });
  const body = h('div', { class: 'activity-body' });
  const steps = h('ol', { class: 'steps', 'aria-live': 'polite', 'aria-label': 'Tool calls' });
  body.append(steps);
  const node = h('details', { class: 'activity', 'data-running': true }, [
    h('summary', {}, [label, flags, h('span', { class: 'activity-chevron', 'aria-hidden': true })]),
    body,
  ]);
  return { node, label, flags, body, steps };
}

function finishActivity(activity, final) {
  const { nodes, flags, label } = renderActivity(final);
  activity.node.removeAttribute('data-running');
  activity.label.textContent = label;
  if (flags) {
    activity.flags.hidden = false;
    activity.flags.textContent = flags === 1 ? '1 audit flag' : `${flags} audit flags`;
    activity.node.setAttribute('data-flagged', '');
  }
  // The audit trail lists the same calls as the live steps, with their arguments.
  activity.body.replaceChildren(...nodes);
  if (!nodes.length) activity.node.remove();
}

// No document_ids are sent: the server then reads every document this conversation holds,
// so a document attached to an earlier question stays in scope for a follow-up, as it
// would in any chat.
async function ask(text, activity, answer) {
  const stop = (node) => { activity.node.remove(); answer.replaceChildren(node); };
  try {
    const response = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      stop(notice('bad', `The server refused the question (${response.status})`, payload.detail || response.statusText));
      return;
    }
    for await (const event of readEvents(response)) {
      if (event.type === 'tool') {
        activity.steps.append(stepNode(event));
        activity.label.textContent = `${didTool(event.tool)}, thinking…`;
      } else if (event.type === 'final') {
        state.sessionId = event.session_id;
        finishActivity(activity, event);
        answer.replaceChildren(...renderAnswer(event));
      } else if (event.type === 'error') {
        stop(notice('bad', 'The run stopped unexpectedly', event.message));
      }
      scrollToEnd();
    }
  } catch (error) {
    stop(notice('bad', 'The connection dropped mid-answer', String(error)));
  }
}

async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (line) yield JSON.parse(line.slice(5).trim());
    }
  }
}

question.focus();
