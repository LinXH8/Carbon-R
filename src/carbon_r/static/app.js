/* Carbon R - the page.
 *
 * One rule governs this file: **the numbers on screen come from `figures`, never from
 * `reply`.** The API returns the agent's prose and, separately, the structured output of
 * `calculate_emissions` with the inputs it used. Scraping a figure out of the prose would
 * put a model-authored number in front of a user - the same defect as letting the model do
 * the arithmetic, just moved into the presentation layer. The prose is rendered as prose,
 * labelled as the agent's wording, and nothing is parsed out of it.
 *
 * Formatting here is digit grouping only. There is no arithmetic in this file.
 */

import { Spring, VelocityTracker, project, rubberband, nearest } from '/static/spring.js';

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
const sheet = $('sheet');
const scrim = $('scrim');
const grabber = $('grabber');
const sheetHead = document.querySelector('.sheet-head');
const sheetBody = $('sheet-body');

const state = {
  sessionId: null,
  documents: [],
  lastCalls: [],
  busy: false,
};

/* --- tiny DOM helper: textContent only, so model prose can never become markup ------ */

function h(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

/* Thousands separators on the integer part; the decimals are passed through verbatim.
   The string is never converted to a Number - a float round-trip is exactly the
   corruption the Decimal discipline on the server exists to prevent. */
function group(decimalString) {
  const [whole, fraction] = String(decimalString).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction ? `${grouped}.${fraction}` : grouped;
}

/* --- upload ------------------------------------------------------------------------ */

dropzone.addEventListener('pointerdown', () => dropzone.setAttribute('data-pressed', ''));
for (const event of ['pointerup', 'pointercancel', 'pointerleave']) {
  dropzone.addEventListener(event, () => dropzone.removeAttribute('data-pressed'));
}
dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.setAttribute('data-dragging', '');
});
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
      showNotice('bad', 'That document was not accepted', payload.detail || response.statusText);
      return;
    }
    state.sessionId = payload.session_id;
    state.documents.push(payload);
    renderDocuments();
    askCard.hidden = false;
  } catch (error) {
    showNotice('bad', 'The upload did not reach the server', String(error));
  } finally {
    dropzone.removeAttribute('data-busy');
  }
}

function renderDocuments() {
  docsList.replaceChildren(
    ...state.documents.map((doc) =>
      h('li', { class: 'chip' }, [
        h('span', { text: doc.filename }),
        h('code', { text: doc.document_id }),
      ]),
    ),
  );
}

/* --- asking, with the tool calls streamed as they happen --------------------------- */

askButton.addEventListener('click', ask);
question.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
});

async function ask() {
  if (state.busy || !question.value.trim()) return;
  setBusy(true);
  steps.replaceChildren();
  answer.replaceChildren();

  let pending = addPendingStep();

  try {
    const response = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: question.value, session_id: state.sessionId }),
    });

    if (!response.ok) {
      pending?.remove();
      const payload = await response.json().catch(() => ({}));
      showNotice('bad', `The server refused the question (${response.status})`,
                 payload.detail || response.statusText);
      return;
    }

    for await (const event of readEvents(response)) {
      if (event.type === 'tool') {
        pending?.remove();
        addToolStep(event);
        pending = addPendingStep();
      } else if (event.type === 'final') {
        pending?.remove();
        renderAnswer(event);
      } else if (event.type === 'error') {
        pending?.remove();
        showNotice('bad', 'The run stopped unexpectedly', event.message);
      }
    }
    pending?.remove();
  } catch (error) {
    pending?.remove();
    showNotice('bad', 'The connection dropped mid-answer', String(error));
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  state.busy = busy;
  askButton.disabled = busy;
  askButton.textContent = busy ? 'Working' : 'Ask';
  askNote.textContent = busy ? 'A vision call, a factor lookup and the calculation.' : '';
}

/* Server-sent events over fetch, so the request can be a POST with a JSON body.
   EventSource would force the question into a query string. */
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

/* --- steps -------------------------------------------------------------------------- */

function addPendingStep() {
  const node = h('li', { class: 'step', 'data-status': 'running' }, [
    h('span', { class: 'step-mark', text: '○', 'aria-hidden': true }),
    h('span', {}, [h('span', { class: 'step-message', text: 'Deciding what to do next…' })]),
  ]);
  steps.append(node);
  return node;
}

const MARKS = { ok: '✓', no_results: '○', error: '×' };

function addToolStep(event) {
  steps.append(
    h('li', { class: 'step', 'data-status': event.status }, [
      h('span', { class: 'step-mark', text: MARKS[event.status] || '•', 'aria-hidden': true }),
      h('span', {}, [
        h('span', { class: 'step-tool', text: event.tool }),
        document.createTextNode(' '),
        h('span', { class: 'step-message', text: event.message }),
      ]),
    ]),
  );
}

/* --- the answer --------------------------------------------------------------------- */

function renderAnswer(final) {
  state.sessionId = final.session_id;
  state.lastCalls = final.tool_calls || [];
  const parts = [];

  // An answer that ran out of turns or lost the provider gets said plainly, and gets no
  // figure panel at all - there is nothing the tools established to put in one.
  if (final.stopped_reason === 'max_turns') {
    parts.push(notice('warn', 'The agent ran out of steps',
      `It used ${final.turns_used} turns and stopped rather than assembling an answer. `
      + 'No figure is shown because none was completed.'));
  } else if (final.stopped_reason === 'provider_error') {
    parts.push(notice('bad', 'The model provider could not be reached',
      'No answer was produced. Nothing below is a figure.'));
  }

  // The figure audit, surfaced rather than logged. Empty is a quiet pass; non-empty is the
  // loudest thing on the page, because it means the reply contains a number no tool
  // produced - the one failure this whole design exists to make visible.
  if (final.unverified_figures?.length) {
    parts.push(notice('bad', 'The reply contains figures no tool produced',
      `Do not use these numbers: ${final.unverified_figures.join(', ')}. `
      + 'They were not returned by any tool in this session.'));
  }

  for (const figure of final.figures || []) parts.push(figureCard(figure));

  if (!final.figures?.length && final.stopped_reason === 'completed') {
    parts.push(notice('plain', 'No figure was calculated',
      'The agent answered without producing a number. That is the expected behaviour when '
      + 'it cannot do something, or when a document or factor is missing.'));
  }

  if (final.reply?.trim()) {
    parts.push(
      h('section', { class: 'card reply' }, [
        h('p', { class: 'label', text: "The agent's wording" }),
        h('p', { class: 'reply-text', text: final.reply }),
      ]),
    );
  }

  if (state.lastCalls.length) {
    const button = h('button', { class: 'button', text: `Audit trail (${state.lastCalls.length} tool calls)` });
    button.addEventListener('click', () => openSheet(button));
    parts.push(h('div', {}, [button]));
  }

  answer.replaceChildren(...parts);
}

function notice(kind, title, detail) {
  return h('div', { class: 'notice', 'data-kind': kind }, [
    h('strong', { text: title }),
    h('span', { text: detail }),
  ]);
}

function figureCard(figure) {
  const f = figure.factor;

  const cite = [
    citeRow('Factor', `${group(f.factor)} ${f.unit}`),
    citeRow('Publisher', f.publisher),
    citeRow('Year used', String(f.year_used),
            f.substituted ? `requested ${f.year_requested}` : null),
    citeRow('Source', f.source_document),
    citeRow('Page or table', f.source_page || 'not paginated'),
    // Always shown, never only when something else is missing: a factor from outside
    // Singapore has to be labelled as one wherever it appears.
    citeRow('Jurisdiction', f.jurisdiction, f.jurisdiction === 'SG' ? null : 'non-Singapore'),
    citeRow('Method', `${figure.method}, ${figure.basis}`),
  ];

  const link = h('div', {}, [
    h('dt', { text: 'Published at' }),
    h('dd', {}, [h('a', { href: f.source_url, target: '_blank', rel: 'noreferrer', text: f.source_url })]),
  ]);

  return h('section', { class: 'card figure-card' }, [
    h('p', {
      class: 'label',
      text: `${group(figure.activity_amount)} ${figure.activity_unit} · ${figure.document_id}`,
    }),
    h('p', { class: 'figure-value' }, [
      document.createTextNode(group(figure.kg_co2e)),
      h('span', { class: 'figure-unit', text: ' kg CO2e' }),
    ]),
    h('p', { class: 'figure-tonnes', text: `${group(figure.tonnes_co2e)} tonnes CO2e` }),
    h('p', { class: 'derivation', text: figure.derivation }),
    h('dl', { class: 'cite' }, [...cite, link]),
    figure.required_disclosures?.length
      ? h('ul', { class: 'disclosures' },
          figure.required_disclosures.map((sentence) => h('li', { text: sentence })))
      : null,
    h('div', { class: 'audit-line' }, [
      h('span', { text: 'Figure audit: every number in the reply was produced by a tool.' }),
      h('span', { text: `${figure.calculation_ref} · ${f.verified ? 'factor verified from primary source' : 'factor not yet verified'}` }),
    ]),
  ]);
}

function citeRow(term, value, badge) {
  return h('div', {}, [
    h('dt', { text: term }),
    h('dd', {}, [
      document.createTextNode(value),
      badge ? h('span', { class: 'badge', text: badge }) : null,
    ]),
  ]);
}

function showNotice(kind, title, detail) {
  answer.replaceChildren(notice(kind, title, detail));
}

/* --- the audit sheet ---------------------------------------------------------------- */
/* The only directly manipulated surface on the page, and so the only animated one.
 *
 * `y` is pixels hidden below the viewport: 0 is fully open, `height` is closed. The spring
 * owns that value; the drag writes into it, and release hands its velocity over so there
 * is no seam between the finger and the animation. */

const SNAP_HALF = 0.45;        // fraction of the sheet left hidden at the half stop
const FLICK = 220;             // px/s above which a release counts as a throw
const HYSTERESIS = 10;         // px of movement before a press becomes a drag

let sheetHeight = 0;
let returnFocusTo = null;

const sheetSpring = new Spring({
  value: 0,
  bounce: 0,
  duration: 0.35,
  onUpdate: (y) => {
    sheet.style.transform = `translate3d(0, ${y}px, 0)`;
    // Dim in proportion to how far open it is: a modal task pushes its context back.
    const openness = sheetHeight ? Math.max(0, Math.min(1, 1 - y / sheetHeight)) : 0;
    scrim.style.opacity = String(openness * 0.4);
  },
  onRest: (y) => {
    if (sheetHeight && y >= sheetHeight - 1) hideSheet();
  },
});

function snapPoints() {
  return [0, sheetHeight * SNAP_HALF, sheetHeight];
}

function renderSheet() {
  if (!state.lastCalls.length) {
    sheetBody.replaceChildren(h('p', { class: 'empty', text: 'No tool calls yet.' }));
    return;
  }
  sheetBody.replaceChildren(
    ...state.lastCalls.map((call) =>
      h('article', { class: 'call', 'data-status': call.status }, [
        h('div', { class: 'call-head' }, [
          h('span', { class: 'call-tool', text: call.tool }),
          h('span', { class: 'call-status', text: call.status }),
        ]),
        h('p', { class: 'call-message', text: call.message }),
        h('pre', { text: prettyArguments(call.arguments) }),
      ]),
    ),
  );
}

function prettyArguments(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;   // unparseable arguments are themselves worth seeing, verbatim
  }
}

function openSheet(trigger) {
  returnFocusTo = trigger || null;
  renderSheet();
  sheet.hidden = false;
  scrim.hidden = false;
  sheet.setAttribute('aria-hidden', 'false');

  sheetHeight = sheet.offsetHeight;
  sheetSpring.set(sheetHeight);              // start closed, off-screen...
  sheetSpring.retarget(0, { bounce: 0, duration: 0.35 });   // ...then open, no overshoot:
  $('sheet-close').focus();                  // a tap carried no momentum to inherit
}

function closeSheet({ velocity } = {}) {
  sheetSpring.retarget(sheetHeight, {
    velocity,
    bounce: 0,
    duration: 0.3,
  });
}

/* Enter and exit along the same path: it arrived from the bottom, it leaves to the bottom. */
function hideSheet() {
  sheet.hidden = true;
  scrim.hidden = true;
  sheet.setAttribute('aria-hidden', 'true');
  returnFocusTo?.focus();
  returnFocusTo = null;
}

$('sheet-close').addEventListener('click', () => closeSheet());
scrim.addEventListener('click', () => closeSheet());
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !sheet.hidden) closeSheet();
});
window.addEventListener('resize', () => {
  if (!sheet.hidden) sheetHeight = sheet.offsetHeight;
});

const tracker = new VelocityTracker();
let drag = null;

function onPointerDown(e) {
  if (e.button !== undefined && e.button !== 0) return;
  // The header holds the Done button. A press there is a press of the button, not a grab
  // of the sheet - capturing the pointer on it would swallow the click.
  if (e.target.closest('button')) return;

  e.target.setPointerCapture?.(e.pointerId);

  // Start from the PRESENTATION value, not from the logical target. Grabbing a sheet that
  // is still moving must pick it up exactly where it is on screen, or it jumps.
  sheetSpring.stop();
  sheetHeight = sheetHeight || sheet.offsetHeight;

  drag = {
    pointerId: e.pointerId,
    startPointer: e.clientY,
    startValue: sheetSpring.value,
    committed: false,
    target: e.target,
  };
  tracker.reset();
  tracker.add(e.clientY);
}

function onPointerMove(e) {
  if (!drag || e.pointerId !== drag.pointerId) return;
  const delta = e.clientY - drag.startPointer;

  // A little hysteresis so a press that drifts by a pixel is still a tap.
  if (!drag.committed) {
    if (Math.abs(delta) < HYSTERESIS) return;
    drag.committed = true;
    sheet.setAttribute('data-dragging', '');
  }

  tracker.add(e.clientY);

  // 1:1 with the finger, offset from where it grabbed, with progressive resistance past
  // either end rather than a hard stop.
  let value = drag.startValue + delta;
  if (value < 0) value = -rubberband(-value, sheetHeight);
  else if (value > sheetHeight) value = sheetHeight + rubberband(value - sheetHeight, sheetHeight);

  sheetSpring.track(value, tracker.velocity);
}

function onPointerUp(e) {
  if (!drag || e.pointerId !== drag.pointerId) return;
  const wasDrag = drag.committed;
  try {
    drag.target.releasePointerCapture?.(e.pointerId);
  } catch {
    // Already released - pointercancel gets here, and there is nothing to undo.
  }
  drag = null;
  sheet.removeAttribute('data-dragging');

  if (!wasDrag) {                       // a tap on the grabber toggles the sheet
    const points = snapPoints();
    closeSheetOrOpen(sheetSpring.value > points[1] ? 0 : sheetHeight);
    return;
  }

  // Land where the gesture was GOING, not where the finger happened to lift.
  const velocity = tracker.velocity;
  const projected = sheetSpring.value + project(velocity);
  const target = nearest(projected, snapPoints());
  const thrown = Math.abs(velocity) > FLICK;

  sheetSpring.retarget(target, {
    velocity,                                   // no seam between drag and animation
    bounce: thrown ? 0.2 : 0,                   // overshoot only because a flick preceded it
    duration: thrown ? 0.3 : 0.35,
  });
}

function closeSheetOrOpen(target) {
  sheetSpring.retarget(target, { bounce: 0, duration: 0.35 });
}

for (const handle of [grabber, sheetHead]) {
  handle.addEventListener('pointerdown', onPointerDown);
}
// On window, so tracking survives the pointer leaving the handle's bounds.
window.addEventListener('pointermove', onPointerMove);
window.addEventListener('pointerup', onPointerUp);
window.addEventListener('pointercancel', onPointerUp);
