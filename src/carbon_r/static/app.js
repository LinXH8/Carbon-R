/* Carbon R - the page: upload, the streamed question, and the answer.
 *
 * The answer is drawn by render.js from the structured `figures` the API returns, never
 * from the agent's prose; nothing here parses a number out of `reply`. Formatting is
 * digit grouping only, done in render.js. There is no arithmetic in this file.
 */

import { h, stepNode, notice, renderAnswer } from '/static/render.js';

const $ = (id) => document.getElementById(id);

const dropzone = $('dropzone');
const fileInput = $('file');
const docsList = $('docs');
const askCard = $('ask-card');
const askButton = $('ask');
const askNote = $('ask-note');
const question = $('question');
const steps = $('steps');
const answer = $('answer');

const state = { sessionId: null, documents: [], busy: false };

/* --- upload -------------------------------------------------------------------------- */

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.setAttribute('data-dragging', ''); });
for (const event of ['dragleave', 'dragend']) {
  dropzone.addEventListener(event, () => dropzone.removeAttribute('data-dragging'));
}
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.removeAttribute('data-dragging');
  const file = e.dataTransfer?.files?.[0];
  if (file) upload(file);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files?.[0]) upload(fileInput.files[0]);
  fileInput.value = '';
});

async function upload(file) {
  dropzone.setAttribute('data-busy', '');
  const body = new FormData();
  body.append('file', file);
  if (state.sessionId) body.append('session_id', state.sessionId);
  try {
    const response = await fetch('/upload', { method: 'POST', body });
    const payload = await response.json();
    if (!response.ok) {
      answer.replaceChildren(notice('bad', 'That document was not accepted', payload.detail || response.statusText));
      return;
    }
    state.sessionId = payload.session_id;
    state.documents.push(payload);
    renderDocuments();
    askCard.hidden = false;
  } catch (error) {
    answer.replaceChildren(notice('bad', 'The upload did not reach the server', String(error)));
  } finally {
    dropzone.removeAttribute('data-busy');
  }
}

function renderDocuments() {
  docsList.replaceChildren(...state.documents.map((doc) => h('li', { class: 'doc' }, [
    h('span', { class: 'doc-name', text: doc.filename }),
    h('code', { class: 'doc-id', text: doc.document_id }),
  ])));
}

/* --- asking -------------------------------------------------------------------------- */

askButton.addEventListener('click', ask);
question.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
});

function setBusy(busy) {
  state.busy = busy;
  askButton.disabled = busy;
  askButton.textContent = busy ? 'Working…' : 'Calculate';
  askNote.textContent = busy ? 'Reading the document, looking up the factor, calculating.' : '';
}

function pendingStep() {
  const node = stepNode({ status: 'running' });
  steps.append(node);
  return node;
}

async function ask() {
  if (state.busy || !question.value.trim()) return;
  setBusy(true);
  steps.replaceChildren();
  answer.replaceChildren();
  let pending = pendingStep();

  try {
    const response = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: question.value, session_id: state.sessionId }),
    });
    if (!response.ok) {
      pending?.remove();
      const payload = await response.json().catch(() => ({}));
      answer.replaceChildren(notice('bad', `The server refused the question (${response.status})`, payload.detail || response.statusText));
      return;
    }
    for await (const event of readEvents(response)) {
      pending?.remove();
      if (event.type === 'tool') {
        steps.append(stepNode(event));
        pending = pendingStep();
      } else if (event.type === 'final') {
        state.sessionId = event.session_id;
        answer.replaceChildren(...renderAnswer(event));
      } else if (event.type === 'error') {
        answer.replaceChildren(notice('bad', 'The run stopped unexpectedly', event.message));
      }
    }
    pending?.remove();
  } catch (error) {
    pending?.remove();
    answer.replaceChildren(notice('bad', 'The connection dropped mid-answer', String(error)));
  } finally {
    setBusy(false);
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
