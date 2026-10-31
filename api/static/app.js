const $ = (id) => document.getElementById(id);
// All API data lives here; renderers never fetch. cy/source are disposable UI resources.
const state = {
  runId: null, health: null, runs: null, report: null, details: null,
  messages: [], topology: null, view: 'console', search: '', acknowledged: readAcknowledged(),
};
let cy = null, source = null;
const ACK_KEY = 'netcoop.acknowledged';

function readAcknowledged() {
  try {
    const saved = JSON.parse(localStorage.getItem('netcoop.acknowledged') || '[]');
    return new Set(Array.isArray(saved) ? saved.filter((id) => typeof id === 'string') : []);
  } catch { return new Set(); }
}
function incidentKey(run) { return run.incident_id || run.run_id; }
function renderTopBar() {
  $('campus-subtitle').textContent = 'Campus Core Grid • ' + (state.report?.incident_id || 'idle');
}
function renderSidebar() {
  const critical = state.runs?.filter((r) => r.status === 'diagnosed' && r.confidence >= 0.75 &&
    !state.acknowledged.has(incidentKey(r)));
  $('critical-badge').hidden = !critical?.length;
  $('critical-badge').textContent = critical ? critical.length + '\nCRIT' : '—';
  $('api-host').textContent = 'HOST: ' + (state.health ? location.host : '—');
  $('api-uptime').textContent = 'UP: ' + (Number.isFinite(state.health?.uptime_s) ? state.health.uptime_s + 's' : '—');
}
// GROUP 2: replace these placeholder renderers with the hero/pipeline UI.
function renderHero() { $('app-version').textContent = state.health?.version ? 'v' + state.health.version : '—'; }
function renderPipeline() {
  $('pipeline-empty').textContent = state.messages.length ? 'Agent messages are available in the timeline below.' :
    state.runId ? 'Waiting for agent messages…' : 'No run yet.';
}
function renderViews() {
  $('main').dataset.view = state.view;
  document.querySelectorAll('[data-view-panel]').forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== state.view; });
  document.querySelectorAll('[data-view-target]').forEach((button) => {
    const active = button.dataset.viewTarget === state.view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
}
function setView(view) {
  if (!$('view-' + view)) return;
  state.view = view; renderViews();
  $('sidebar').classList.remove('open');
  $('sidebar-toggle').setAttribute('aria-expanded', 'false');
  if (view === 'console' && cy) { cy.resize(); cy.fit(); }
}
function renderSearch() {
  // Later bus/telemetry renderers can use state.search or mark rows data-searchable.
  document.querySelectorAll('#timeline > li, #evidence > li, [data-searchable]').forEach((row) => {
    row.hidden = !row.textContent.toLowerCase().includes(state.search);
  });
}
function renderShell() { renderTopBar(); renderSidebar(); renderHero(); renderPipeline(); }

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
  const results = await Promise.allSettled([json('/api/scenarios'), refreshShellData()]);
  if (results[0].status === 'rejected') throw results[0].reason;
  const list = results[0].value;
  list.forEach((s) => {
    const o = el('option', s.name + '  (' + s.expected + ')'); o.value = s.name; $('scenario').append(o);
  });
  $('scenario').value = 'congestion';
}

// Single data-loading/subscription layer: all endpoint calls stay in these helpers.
async function refreshShellData() {
  const results = await Promise.allSettled([json('/api/health'), json('/api/runs')]);
  if (results[0].status === 'fulfilled') state.health = results[0].value;
  if (results[1].status === 'fulfilled') state.runs = results[1].value;
  renderShell();
}
async function loadRunData() {
  state.report = await json(`/api/runs/${state.runId}/report`);
  const results = await Promise.allSettled([
    json(`/api/runs/${state.runId}/details`), json(`/api/runs/${state.runId}/topology`), refreshShellData(),
  ]);
  state.details = results[0].status === 'fulfilled' ? results[0].value : null;
  state.topology = results[1].status === 'fulfilled' ? results[1].value : null;
  renderShell(); renderReport(); drawTopology(); renderSearch();
}
function subscribeRun() {
  source = new EventSource(`/api/runs/${state.runId}/stream`);
  source.onmessage = (e) => {
    try { addMessage(JSON.parse(e.data)); }
    catch { $('status').textContent = 'Error: unreadable stream message'; }
  };
  source.addEventListener('done', async () => {
    source.close();
    try { await loadRunData(); $('status').textContent = 'Run ' + state.runId + ' complete'; }
    catch (err) { $('status').textContent = 'Error: ' + err.message; }
    $('run').disabled = false;
  });
  source.onerror = () => {
    source.close(); $('run').disabled = false;
    $('status').textContent = 'Stream disconnected. Start a new run to retry.';
  };
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
  state.messages.push(msg);
  renderPipeline(); renderTimeline(msg);
}
function renderTimeline(msg) {
  const tl = $('timeline');
  if (tl.firstChild && tl.firstChild.classList.contains('muted')) tl.replaceChildren();
  const li = el('li');
  li.append(el('span', msg.type, 'tag ' + msg.type), el('b', msg.sender + ' → ' + msg.recipient + ' '),
            el('span', summarize(msg), 'muted'));
  tl.append(li); tl.scrollTop = tl.scrollHeight;
  renderSearch();
}

function drawTopology() {
  const t = state.topology;
  if (!t) { $('topo').textContent = 'No topology available.'; return; }
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

function renderReport() {
  const r = state.report;
  if (!r) { $('report').hidden = true; return; }
  $('report').hidden = false;
  const s = $('summary'); s.replaceChildren();
  s.append(el('div', r.suspected_root_cause || '—', 'big'));
  const confidence = Number.isFinite(r.confidence) ? (r.confidence * 100).toFixed(0) + '%' : '—';
  const meta = el('div', 'cause: ' + (r.cause || '—') + ' · confidence ' + confidence);
  meta.append(el('span', r.confidence_category || '—', 'pill'), el('span', r.status || '—', 'pill'));
  s.append(meta, el('div', 'Affected: ' + ((r.affected_devices || []).join(', ') || '—'), 'muted'));
  fill('evidence', r.supporting_evidence, (e) =>
    `${e.device_id ? e.device_id + ' · ' : ''}${e.metric} = ${e.value}${e.unit ? ' ' + e.unit : ''} — ${e.interpretation || ''}`);
  fill('actions', r.recommended_actions, (a) => (typeof a === 'string' ? a : JSON.stringify(a)));
  fill('alts', r.alternative_causes, (a) => (typeof a === 'string' ? a : `${a.cause || a.name}: ${a.confidence ?? a.score ?? ''}`));
  fill('limits', r.limitations, (x) => x);
  $('fixresult').textContent = '';
  $('fix').hidden = !['congestion', 'weak_wifi', 'interface_fault', 'device_overload', 'dns_failure',
    'dhcp_failure', 'hardware_failure', 'latency_degradation'].includes(r.cause);
}

async function run() {
  $('run').disabled = true; $('status').textContent = 'Starting…'; $('report').hidden = true;
  state.runId = null; state.report = null; state.details = null; state.topology = null; state.messages = [];
  if (cy) { cy.destroy(); cy = null; }
  $('topo').textContent = '';
  setView('console'); renderShell();
  $('timeline').replaceChildren(el('li', 'Waiting for messages…', 'muted'));
  if (source) source.close();
  try {
    const body = { fail_agent: $('fail').value || null };
    const f = $('file').files[0];
    if (f) body.upload_id = (await json('/api/upload', { method: 'POST', body: await f.text() })).upload_id;
    else body.scenario = $('scenario').value;
    state.runId = (await json('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) })).run_id;
    $('status').textContent = 'Run ' + state.runId;
    renderShell(); subscribeRun();
  } catch (err) {
    $('status').textContent = 'Error: ' + err.message; $('run').disabled = false;
  }
}

$('run').onclick = run;
$('fix').onclick = async () => {
  $('fix').disabled = true;
  try {
    const r = await json(`/api/runs/${state.runId}/remediate`, { method: 'POST' });
    const out = $('fixresult');
    out.textContent = `Simulated fix: ${r.before} → ${r.after} — ${r.verified ? 'verified' : 'NOT verified'}`;
    out.className = r.verified ? 'pass' : 'fail';
  } catch (err) { $('fixresult').textContent = 'Error: ' + err.message; $('fixresult').className = 'fail'; }
  $('fix').disabled = false;
  await refreshShellData();
};
document.querySelectorAll('[data-view-target]').forEach((button) => {
  button.addEventListener('click', (event) => {
    event.preventDefault(); setView(button.dataset.viewTarget);
    if (button.dataset.focus) { $(button.dataset.focus).focus(); $(button.dataset.focus).scrollIntoView({ block: 'start' }); }
  });
});
$('sidebar-toggle').onclick = () => {
  const open = $('sidebar').classList.toggle('open');
  $('sidebar-toggle').setAttribute('aria-expanded', String(open));
};
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { $('sidebar').classList.remove('open'); $('sidebar-toggle').setAttribute('aria-expanded', 'false'); }
});
$('spawn-run').onclick = () => {
  if (typeof window.openRunModal === 'function') { window.openRunModal(); return; }
  setView('console'); $('run-controls').scrollIntoView({ block: 'start' }); $('scenario').focus();
};
$('quarantine').onclick = () => {
  if (typeof window.openRemediateModal === 'function') { window.openRemediateModal(); return; }
  setView('console');
  if (!$('fix').hidden) { $('fix').scrollIntoView({ block: 'center' }); $('fix').focus(); }
  else { $('status').textContent = 'Run a supported diagnosis to approve simulated remediation.'; $('run-controls').scrollIntoView({ block: 'start' }); }
};
$('acknowledge-all').onclick = () => {
  // Acknowledge all currently listed incidents, including the selected run.
  const runs = [...(state.runs || [])];
  if (state.report) runs.push({ ...state.report, run_id: state.runId });
  runs.forEach((r) => { const key = incidentKey(r); if (key) state.acknowledged.add(key); });
  try { localStorage.setItem(ACK_KEY, JSON.stringify([...state.acknowledged])); }
  catch { $('status').textContent = 'Acknowledged for this session; browser storage is unavailable.'; }
  renderSidebar();
};
$('search').addEventListener('input', () => { state.search = $('search').value.trim().toLowerCase(); renderSearch(); });
renderViews(); renderShell();
init().catch((e) => { $('status').textContent = 'Error: ' + e.message; });
