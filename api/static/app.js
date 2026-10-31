const $ = (id) => document.getElementById(id);
let runId = null, cy = null, source = null;

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function fill(id, items, fmt) {
  const ul = $(id); ul.replaceChildren();
  (items && items.length ? items : ['None']).forEach((i) => ul.append(el('li', items && items.length ? fmt(i) : i)));
}
async function json(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

async function init() {
  const list = await json('/api/scenarios');
  list.forEach((s) => {
    const o = el('option', s.name + '  (' + s.expected + ')'); o.value = s.name; $('scenario').append(o);
  });
  $('scenario').value = 'congestion';
}

function summarize(msg) {
  const p = msg.payload || {};
  if (p.message) return p.message;
  if (p.query_type || p.type) return p.query_type || p.type;
  if (p.diagnosis && p.diagnosis.cause) return 'diagnosis: ' + p.diagnosis.cause;
  if (p.report) return 'report ready';
  if (p.hypotheses && p.hypotheses.length) return 'top hypothesis: ' + (p.hypotheses[0].cause || p.hypotheses[0].name || '');
  if (p.data_quality) return 'data quality ' + p.data_quality.score;
  if (p.raw !== undefined) return 'raw telemetry submitted';
  return Object.keys(p).slice(0, 4).join(', ');
}
function addMessage(msg) {
  const tl = $('timeline');
  if (tl.firstChild && tl.firstChild.classList.contains('muted')) tl.replaceChildren();
  const li = el('li');
  li.append(el('span', msg.type, 'tag ' + msg.type), el('b', msg.sender + ' → ' + msg.recipient + ' '),
            el('span', summarize(msg), 'muted'));
  tl.append(li); tl.scrollTop = tl.scrollHeight;
}

async function drawTopology() {
  const t = await json(`/api/runs/${runId}/topology`);
  const bad = new Set(t.affected);
  const cs = getComputedStyle(document.documentElement);
  const col = (n) => cs.getPropertyValue(n).trim();
  if (cy) cy.destroy();
  if (!t.nodes.length) { $('topo').textContent = 'No topology supplied.'; cy = null; return; }
  $('topo').textContent = '';
  cy = cytoscape({
    container: $('topo'),
    elements: [
      ...t.nodes.map((n) => ({ data: { id: n.id, label: n.id, bad: bad.has(n.id) ? 1 : 0 } })),
      ...t.links.map((l, i) => ({ data: { id: 'e' + i, source: l.src, target: l.dst } })),
    ],
    style: [
      { selector: 'node', style: { label: 'data(label)', 'font-size': 9, color: col('--fg'), 'text-valign': 'bottom',
        'background-color': col('--ok') } },
      { selector: 'node[bad = 1]', style: { 'background-color': col('--bad'), width: 28, height: 28 } },
      { selector: 'edge', style: { width: 2, 'line-color': col('--muted') } },
    ],
    layout: { name: 'breadthfirst', directed: true, padding: 10 },
  });
}

async function showReport() {
  const r = await json(`/api/runs/${runId}/report`);
  $('report').hidden = false;
  const s = $('summary'); s.replaceChildren();
  s.append(el('div', r.suspected_root_cause || 'Unknown', 'big'));
  const meta = el('div', 'cause: ' + r.cause + ' · confidence ' + (r.confidence * 100).toFixed(0) + '%');
  meta.append(el('span', r.confidence_category, 'pill'), el('span', r.status, 'pill'));
  s.append(meta, el('div', 'Affected: ' + ((r.affected_devices || []).join(', ') || 'none'), 'muted'));
  fill('evidence', r.supporting_evidence, (e) =>
    `${e.device_id ? e.device_id + ' · ' : ''}${e.metric} = ${e.value}${e.unit ? ' ' + e.unit : ''} — ${e.interpretation || ''}`);
  fill('actions', r.recommended_actions, (a) => (typeof a === 'string' ? a : JSON.stringify(a)));
  fill('alts', r.alternative_causes, (a) => (typeof a === 'string' ? a : `${a.cause || a.name}: ${a.confidence ?? a.score ?? ''}`));
  fill('limits', r.limitations, (x) => x);
  $('fixresult').textContent = '';
  $('fix').hidden = !['congestion', 'weak_wifi', 'interface_fault', 'device_overload', 'dns_failure',
    'dhcp_failure', 'hardware_failure', 'latency_degradation'].includes(r.cause);
  await drawTopology();
}

async function run() {
  $('run').disabled = true; $('status').textContent = 'Starting…'; $('report').hidden = true;
  $('timeline').replaceChildren(el('li', 'Waiting for messages…', 'muted'));
  if (source) source.close();
  try {
    const body = { fail_agent: $('fail').value || null };
    const f = $('file').files[0];
    if (f) body.upload_id = (await json('/api/upload', { method: 'POST', body: await f.text() })).upload_id;
    else body.scenario = $('scenario').value;
    runId = (await json('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) })).run_id;
    $('status').textContent = 'Run ' + runId;
    source = new EventSource(`/api/runs/${runId}/stream`);
    source.onmessage = (e) => addMessage(JSON.parse(e.data));
    source.addEventListener('done', async () => {
      source.close();
      try { await showReport(); $('status').textContent = 'Run ' + runId + ' complete'; }
      catch (err) { $('status').textContent = 'Error: ' + err.message; }
      $('run').disabled = false;
    });
    source.onerror = () => { source.close(); $('run').disabled = false; };
  } catch (err) {
    $('status').textContent = 'Error: ' + err.message; $('run').disabled = false;
  }
}

$('run').onclick = run;
$('fix').onclick = async () => {
  $('fix').disabled = true;
  try {
    const r = await json(`/api/runs/${runId}/remediate`, { method: 'POST' });
    const out = $('fixresult');
    out.textContent = `Simulated fix: ${r.before} → ${r.after} — ${r.verified ? 'verified' : 'NOT verified'}`;
    out.className = r.verified ? 'pass' : 'fail';
  } catch (err) { $('fixresult').textContent = 'Error: ' + err.message; $('fixresult').className = 'fail'; }
  $('fix').disabled = false;
};
init().catch((e) => { $('status').textContent = 'Error: ' + e.message; });
