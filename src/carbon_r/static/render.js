/* Carbon R - the answer renderer.
 *
 * One rule governs this file: **the numbers on screen come from `figures`, never from
 * `reply`.** Nothing is parsed out of the prose, nothing is converted to a Number, and the
 * only formatting is digit grouping. Every node is built with textContent.
 *
 * A figure is laid out as a statement: the total first, then its caveats, its working
 * and one citation per line. Without any interaction it shows every factor's value, unit,
 * publisher, year (used and requested), source document, page, jurisdiction, verification
 * state, and every caveat sentence the server sent.
 */

export function h(tag, props = {}, children = []) {
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

/* Thousands separators on the integer part; the decimals are passed through verbatim. */
export function group(decimalString) {
  const [whole, fraction] = String(decimalString).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction ? `${grouped}.${fraction}` : grouped;
}

/* --- steps ---------------------------------------------------------------------------- */

const MARKS = { ok: '✓', no_results: '○', error: '×', running: '·' };
const STATUS_WORDS = { ok: 'ok', no_results: 'no results', error: 'error', running: 'working' };

export function stepNode({ tool, status, message }) {
  return h('li', { class: 'step', 'data-status': status }, [
    h('span', { class: 'step-mark', text: MARKS[status] || '•', 'aria-hidden': true }),
    h('span', { class: 'step-tool', text: tool || 'Deciding what to do next' }),
    h('span', { class: 'step-status', text: STATUS_WORDS[status] || status }),
    message ? h('span', { class: 'step-message', text: message }) : null,
  ]);
}

export function notice(kind, title, detail) {
  return h('div', { class: 'notice', 'data-kind': kind, role: kind === 'bad' ? 'alert' : null }, [
    h('strong', { text: title }),
    h('span', { text: detail }),
  ]);
}

/* --- the answer ----------------------------------------------------------------------- */

export function renderAnswer(final) {
  const parts = [];

  if (final.stopped_reason === 'max_turns') {
    parts.push(notice('warn', 'The agent ran out of steps',
      `It used ${final.turns_used} turns and stopped rather than assembling an answer. `
      + 'No figure is shown because none was completed.'));
  } else if (final.stopped_reason === 'provider_error') {
    parts.push(notice('bad', 'The model provider could not be reached',
      'No answer was produced. Nothing below is a figure.'));
  }

  const flaggedFigures = final.unverified_figures || [];
  const flaggedQuotes = final.unverified_quotes || [];
  if (flaggedFigures.length) {
    parts.push(notice('bad', 'The reply contains figures no tool produced',
      `Do not use these numbers: ${flaggedFigures.join(', ')}. `
      + 'They were not returned by any tool in this session.'));
  }
  if (flaggedQuotes.length) {
    parts.push(notice('bad', 'The reply quotes words no source contains',
      'These quotations were not found in any passage a tool returned: '
      + `${flaggedQuotes.map((q) => `"${q}"`).join('; ')}. Do not rely on them.`));
  }

  // The audit sentence on a figure states what the audit found. app.js printed the pass
  // sentence unconditionally, under a red notice saying the opposite.
  const audit = flaggedFigures.length || flaggedQuotes.length ? 'fail' : 'pass';
  for (const figure of final.figures || []) parts.push(statementFigure(figure, audit));

  if (!final.figures?.length && final.stopped_reason === 'completed') {
    parts.push(notice('plain', 'No figure was calculated',
      'The agent answered without producing a number. That is the expected behaviour when '
      + 'it cannot do something, or when a document or factor is missing.'));
  }

  if (final.reply?.trim()) {
    parts.push(h('section', { class: 'reply', 'aria-label': "The agent's wording" }, [
      h('h3', { class: 'section-label', text: "The agent's wording" }),
      h('p', { class: 'reply-text', text: final.reply }),
      h('p', { class: 'reply-note', text: 'Prose from the model. The figures above are the record; nothing is taken from this text.' }),
    ]));
  }

  if (final.tool_calls?.length) parts.push(auditTrail(final.tool_calls));
  return parts;
}

/* Every caveat for a figure, in one list: the server's disclosure sentences first, then
   the per-factor facts a reader needs next to the number - a substituted year (with the
   server's own note), and a factor not yet verified from its primary source. */
function caveatsFor(figure) {
  const items = [...(figure.required_disclosures || [])];
  for (const line of figure.lines || []) {
    const f = line.factor;
    // Prefixed with the activity only when there is more than one to tell apart.
    const lead = (text) => (figure.lines.length > 1 ? `${line.kind}: ${text}` : text);
    if (f.substituted) {
      items.push(lead(`Factor year ${f.year_used} used for activity year ${f.year_requested}.`)
        + (f.substitution_note ? ` ${f.substitution_note}` : ''));
    }
    if (!f.verified) items.push(lead("This factor has not yet been read from the publisher's own document."));
  }
  return items;
}

function auditFooter(figure, audit) {
  const lines = figure.lines || [];
  const verified = lines.every((l) => l.factor.verified);
  return h('footer', { class: 'figure-audit', 'data-state': audit }, [
    h('span', { class: 'audit-mark', text: audit === 'pass' ? '✓' : '×', 'aria-hidden': true }),
    h('span', {
      class: 'audit-text',
      text: audit === 'pass'
        ? 'Figure audit passed: every number in the reply was produced by a tool.'
        : 'Figure audit failed: the reply states something no tool produced. This figure is the tool output; the reply is not.',
    }),
    h('span', { class: 'audit-ref', text: `${figure.calculation_ref} · ${verified ? 'factors verified from primary source' : 'a factor is not yet verified'}` }),
  ]);
}

function provenance(line) {
  const f = line.factor;
  const rows = [
    ['Factor', `${group(f.factor)} ${f.unit}`],
    ['Year', `${f.year_used}`, f.substituted ? `requested ${f.year_requested}` : null],
    ['Publisher', f.publisher],
    ['Source', f.source_document],
    ['Page or table', f.source_page || 'not paginated'],
    ['Jurisdiction', f.jurisdiction, f.jurisdiction === 'SG' ? null : 'non-Singapore'],
    ['Classification', [line.scope ? `Scope ${line.scope}` : null, line.method, line.gas_basis].filter(Boolean).join(', ')],
    ['Verification', f.verified ? "Read from the publisher's own document" : 'Not yet verified from primary source', f.verified ? null : 'unverified'],
    ['Retrieved', f.retrieved_at],
    f.notes ? ['Notes', f.notes] : null,
  ].filter(Boolean);

  return h('dl', { class: 'prov' }, [
    ...rows.map(([term, value, badge]) => h('div', { class: 'prov-row' }, [
      h('dt', { text: term }),
      h('dd', {}, [document.createTextNode(value), badge ? h('span', { class: 'badge', text: badge }) : null]),
    ])),
    h('div', { class: 'prov-row' }, [
      h('dt', { text: 'Published at' }),
      h('dd', {}, [h('a', { href: f.source_url, target: '_blank', rel: 'noreferrer', text: f.source_url })]),
    ]),
  ]);
}

function inputNotes(line) {
  return [
    line.activity_derivation ? h('p', { class: 'sub-derivation', text: `Quantity: ${line.activity_derivation}` }) : null,
    line.conversion ? h('p', { class: 'sub-derivation', text: `Conversion: ${line.conversion.statement} (${line.conversion.source})` }) : null,
  ];
}

function scopeSplit(figure) {
  const byScope = Object.entries(figure.kg_co2e_by_scope || {});
  if (byScope.length < 2) return null;
  return h('ul', { class: 'scope-split' }, byScope.map(([scope, kg]) => h('li', {}, [
    h('span', { text: `Scope ${scope}` }),
    h('strong', { class: 'num', text: `${group(kg)} kg CO2e` }),
  ])));
}

/* --- statement: the total, then its caveats, then its working and citations ---------- */

function statementFigure(figure, audit) {
  const lines = figure.lines || [];
  const single = lines.length === 1;
  const kinds = lines.map((l) => l.kind).join(' + ');
  const scopes = (figure.scopes || []).map((n) => `Scope ${n}`).join(' + ');
  const caveats = caveatsFor(figure);

  return h('article', { class: 'figure', 'data-audit': audit }, [
    h('header', { class: 'figure-head' }, [
      h('p', { class: 'eyebrow', text: `${kinds} · ${scopes} · ${(figure.document_ids || []).join(', ')}` }),
      h('p', { class: 'figure-value' }, [
        h('span', { class: 'figure-num num', text: group(figure.kg_co2e) }),
        h('span', { class: 'figure-unit', text: ' kg CO2e' }),
      ]),
      h('p', { class: 'figure-alt num', text: `${group(figure.tonnes_co2e)} tonnes CO2e` }),
      h('p', { class: 'figure-method', text: [...(figure.methods || []), ...(figure.gas_bases || [])].join(' · ') }),
    ]),
    caveats.length ? h('section', { class: 'caveats', 'aria-label': 'Read with this figure' }, [
      h('h3', { class: 'section-label', text: 'Read with this figure' }),
      h('ul', {}, caveats.map((text) => h('li', { text }))),
    ]) : null,
    h('section', { class: 'working' }, [
      h('h3', { class: 'section-label', text: 'Working' }),
      h('p', { class: 'derivation', text: figure.derivation }),
      scopeSplit(figure),
    ]),
    ...lines.map((line) => h('section', { class: 'line' }, [
      h('h3', { class: 'line-head' }, [
        h('span', { class: 'line-title', text: `${group(line.activity_amount)} ${line.activity_unit} of ${line.kind}` }),
        single ? null : h('span', { class: 'line-result num', text: `${group(line.kg_co2e)} kg CO2e` }),
      ]),
      ...inputNotes(line),
      single ? null : h('p', { class: 'derivation', text: line.derivation }),
      provenance(line),
    ])),
    auditFooter(figure, audit),
  ]);
}

/* --- audit trail: inline and keyboard-reachable, not a sheet behind a gesture -------- */

function auditTrail(calls) {
  return h('details', { class: 'trail' }, [
    h('summary', { text: `Audit trail · ${calls.length} tool calls` }),
    h('ol', { class: 'trail-list' }, calls.map((call) => h('li', { class: 'call', 'data-status': call.status }, [
      h('div', { class: 'call-head' }, [
        h('span', { class: 'call-tool', text: call.tool }),
        h('span', { class: 'call-status', text: call.status }),
      ]),
      h('p', { class: 'call-message', text: call.message }),
      h('pre', { text: prettyArguments(call.arguments) }),
    ]))),
  ]);
}

function prettyArguments(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
