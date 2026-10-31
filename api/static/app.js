const $ = (id) => document.getElementById(id);
// All API data lives here; renderers never fetch. cy/source are disposable UI resources.
const state = {
  runId: null, health: null, runs: null, report: null, details: null,
  messages: [], consoleMessages: [], topology: null, view: 'console', search: '', acknowledged: readAcknowledged(),
  running: false, scenariosReady: false, remediation: null, remediating: false,
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
const PIPELINE_AGENTS = [
  ['normalizer', 'INGESTION'], ['performance', 'PERFORMANCE'],
  ['infrastructure', 'INFRASTRUCTURE'], ['connectivity', 'TOPOLOGY'], ['correlator', 'RCA'],
];
const AGENT_STATUSES = new Set(['pending', 'running', 'complete', 'flagged', 'alert', 'failed', 'abstained', 'synthesizing']);
function relativeTime(stamp) {
  const end = Date.parse(stamp);
  if (!Number.isFinite(end)) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - end) / 1000));
  if (seconds < 60) return seconds + 's ago';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  return Math.floor(seconds / 86400) + 'd ago';
}
function renderHero() {
  const r = state.report, d = state.details;
  $('app-version').textContent = state.health?.version ? 'v' + state.health.version : '—';
  $('trigger-run').disabled = state.running || !state.scenariosReady;
  $('trigger-run').classList.toggle('idle-highlight', !state.runId && !state.running);
  $('export-toggle').disabled = !r || state.running;
  $('hero-stats').classList.toggle('is-idle', !state.runId && !state.running);
  let health = '—', tone = '';
  if (r?.status === 'diagnosed' && r.cause === 'normal') { health = 'Healthy'; tone = 'complete'; }
  else if (r?.status === 'error' || (r?.status === 'diagnosed' && r.cause && r.confidence >= 0.75)) {
    health = 'Critical'; tone = 'alert';
  } else if (r) { health = 'Degraded'; tone = 'flagged'; }
  $('mesh-health').textContent = health === '—' ? health : 'Campus Mesh: ' + health;
  $('mesh-health').className = tone;
  $('mesh-health-dot').className = 'health-dot ' + tone;
  // Abstaining agents still produced a finding; unavailable agents did not.
  const produced = new Set(state.messages.filter((m) => m.type === 'FINDING').map((m) => m.sender));
  const agents = d?.agents;
  const count = state.messages.length ? PIPELINE_AGENTS.filter(([name]) => produced.has(name)).length :
    agents ? agents.filter((a) => a.status !== 'failed' &&
      (a.name !== 'correlator' || a.top_hypothesis != null)).length : null;
  $('swarm-status').textContent = count === null ? '—' : `Swarm Status: ${count} Active Diagnostic Agents`;
  $('swarm-status').className = count === null ? '' : 'complete';
  $('swarm-status-dot').className = 'health-dot' + (count === null ? '' : ' complete');
  const since = relativeTime(d?.finished_at);
  $('coordination-cycle').textContent = since === '—' ? '—' : 'Last Coordinated Sweep: ' + since;
  $('query-rounds').textContent = Number.isFinite(d?.rounds) ? d.rounds + ' query rounds' : '—';
  $('active-incident').textContent = r?.incident_id ? 'Active Incident: ' + r.incident_id : '—';
  const severity = $('incident-severity');
  severity.hidden = !r;
  const high = r?.status === 'diagnosed' && r.cause && r.cause !== 'normal' && r.confidence >= 0.75;
  const medium = r?.confidence_category === 'medium';
  const low = ['low', 'insufficient'].includes(r?.confidence_category);
  severity.textContent = high ? 'P1 CRIT' : medium ? 'P2' : low ? 'P3' : '—';
  severity.className = 'pill ' + (high ? 'alert' : medium ? 'flagged' : '');
}
// SSE is the live source; /details replaces these projections after the run ends.
function liveAgent(name) {
  const view = { name, status: 'pending', summary: '', stats: {} };
  for (const msg of state.messages) {
    const p = msg.payload || {};
    if ((msg.type === 'TASK' && msg.recipient === name) ||
        (msg.type === 'QUERY' && (msg.recipient === name || p.target_agent === name))) view.status = 'running';
    if (name === 'correlator' && ((msg.type === 'QUERY' && msg.sender === name) ||
        (msg.type === 'RESPONSE' && msg.recipient === name))) view.status = 'synthesizing';
    if (msg.sender !== name) continue;
    if (msg.type === 'ERROR') { view.status = 'failed'; view.summary = p.message || ''; continue; }
    if (msg.type === 'RESPONSE' && name !== 'correlator') {
      view.status = view.findingStatus || 'complete'; continue;
    }
    if (msg.type !== 'FINDING') continue;
    if (name === 'normalizer') {
      view.status = Number.isFinite(p.data_quality?.score) && p.data_quality.score < 0.5 ? 'flagged' : 'complete';
      view.summary = p.formats?.length ? 'Normalized ' + p.formats.join(', ') + ' telemetry' : '';
      if (Number.isFinite(p.data_quality?.score)) view.summary += '; data quality ' + Math.round(p.data_quality.score * 100) + '%';
      view.stats = { Ingested: Array.isArray(p.telemetry?.samples) ? p.telemetry.samples.length + ' samples' : '—',
        Mapped: Array.isArray(p.mappings) ? p.mappings.length + ' fields' : '—' };
    } else if (name === 'correlator') {
      const diagnosis = p.diagnosis || {};
      view.status = diagnosis.cause && diagnosis.cause !== 'normal' ?
        diagnosis.status === 'diagnosed' && diagnosis.confidence >= 0.75 ? 'alert' : 'flagged' : 'complete';
      view.summary = diagnosis.cause ? 'Top hypothesis: ' + diagnosis.cause : '';
      view.stats = { Confidence: Number.isFinite(diagnosis.confidence) ? Math.round(diagnosis.confidence * 100) + '%' : '—',
        Conflicts: Array.isArray(diagnosis.conflicts) ? diagnosis.conflicts.length : '—' };
    } else {
      const top = [...(p.hypotheses || [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
      // Critical classifications without an explicit SSE severity arrive via /details.
      const critical = p.evidence?.some((e) => e.severity === 'critical' || /(?:>=|<=) critical\b/.test(e.interpretation || ''));
      view.status = !top ? 'abstained' : top.cause === 'normal' ? 'complete' : critical ? 'alert' : 'flagged';
      view.summary = [top?.cause ? 'Top hypothesis: ' + top.cause +
        (Number.isFinite(top.score) ? ' (' + Math.round(top.score * 100) + '%)' : '') : '', ...(p.notes || []).slice(0, 1)].filter(Boolean).join('. ');
      view.stats = { Evidence: Array.isArray(p.evidence) ? p.evidence.length : '—',
        Flagged: Array.isArray(p.affected_devices) ? p.affected_devices.length + ' devices' : '—' };
    }
    view.findingStatus = view.status;
  }
  return view;
}
function renderPipeline() {
  const agreement = state.details?.consensus;
  $('pipeline-consensus').textContent = agreement && typeof agreement.synchronized === 'boolean' ?
    `${agreement.synchronized ? 'SYNCHRONIZED' : 'DIVERGENT'} (${agreement.engaged ?? '—'}/${agreement.total ?? '—'} AGENTS ENGAGED)` : '—';
  $('pipeline-consensus').className = agreement ? agreement.synchronized ? 'synchronized' : 'divergent' : '';
  const grid = $('pipeline-agents');
  grid.classList.toggle('is-idle', !state.runId && !state.running);
  grid.replaceChildren();
  PIPELINE_AGENTS.forEach(([name, category], i) => {
    const agent = state.details?.agents?.find((a) => a.name === name) || liveAgent(name);
    const status = AGENT_STATUSES.has(agent.status) ? agent.status : 'pending';
    const card = el('article', undefined, 'agent-card ' + status);
    card.id = 'agent-' + name;
    const header = el('div', undefined, 'agent-heading');
    header.append(el('span', '// AGENT-' + String(i + 1).padStart(2, '0') + ':', 'mono agent-index'),
      el('span', status[0].toUpperCase() + status.slice(1), 'pill ' + status));
    const title = el('h3', name[0].toUpperCase() + name.slice(1) + ' Agent');
    const summary = el('p', agent.summary || '—', 'agent-summary');
    summary.title = agent.summary || '';
    const footer = el('div', undefined, 'agent-stats mono');
    const stats = Object.entries(agent.stats || {}).slice(0, 2);
    for (let j = 0; j < 2; j++) {
      const stat = el('span');
      if (stats[j]) stat.append(el('span', stats[j][0] + ': ', 'muted'), el('strong', stats[j][1] ?? '—'));
      else stat.textContent = '—';
      footer.append(stat);
    }
    card.append(header, el('p', category, 'agent-category mono'), title, summary, footer);
    grid.append(card);
  });
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
  document.querySelectorAll('#evidence > li, [data-searchable]').forEach((row) => {
    row.hidden = !row.textContent.toLowerCase().includes(state.search);
  });
  const visible = [...$('bus-messages').children].some((row) => !row.hidden);
  $('bus-empty').hidden = visible;
  $('bus-empty').textContent = state.search ? 'No matching messages.' : state.running ? 'Waiting for run messages…' : 'No run messages yet.';
}
function renderShell() { renderTopBar(); renderSidebar(); renderHero(); renderPipeline(); renderReport(); renderRightRail(); renderBus(); }

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
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
  state.scenariosReady = true; renderHero();
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
  renderShell(); drawTopology(); renderSearch();
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
    state.running = false; $('run').disabled = false; renderShell();
  });
  source.onerror = () => {
    source.close(); state.running = false; $('run').disabled = false; renderShell();
    $('status').textContent = 'Stream disconnected. Start a new run to retry.';
  };
}

function summarize(msg) {
  const p = msg.payload || {};
  if (p.message) return p.message;
  if (p.action) return [p.action, p.reason].filter(Boolean).join(': ');
  if (p.query_type || p.type) return p.query_type || p.type;
  if (p.diagnosis && p.diagnosis.cause) return 'diagnosis: ' + p.diagnosis.cause;
  if (p.report) return 'report ready';
  if (p.hypotheses && p.hypotheses.length) {
    const top = [...p.hypotheses].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    return ['top hypothesis: ' + (top.cause || top.name || '—'), ...(p.notes || []).slice(0, 1)].join('; ');
  }
  if (p.notes?.length) return p.notes.join('; ');
  if (p.data_quality) return 'data quality ' + p.data_quality.score;
  if (p.raw !== undefined) return 'raw telemetry submitted';
  return Object.keys(p).slice(0, 4).join(', ');
}
function addMessage(msg) {
  state.messages.push(msg);
  renderHero(); renderPipeline(); renderRightRail(); renderBus();
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

// GROUP 3: report renderers consume only state.report and state.details.
const REMEDIABLE_CAUSES = new Set(['congestion', 'weak_wifi', 'interface_fault', 'device_overload',
  'dns_failure', 'dhcp_failure', 'hardware_failure', 'latency_degradation']);
const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
const percent = (value) => Number.isFinite(value) ? Math.round(value * 100) + '%' : '—';
function metricText(metric, value, unit) {
  return display(metric) + ' = ' + display(value) + (unit ? ' ' + unit : '');
}
function evidenceTone(e) {
  const device = state.details?.devices?.find((d) => d.device_id === e.device_id && d.worst_metric === e.metric);
  if (device) return device.severity === 'critical' ? 'alert' : device.severity === 'warning' ? 'flagged' : 'complete';
  return /critical/i.test(e.interpretation || '') ? 'alert' : /warning/i.test(e.interpretation || '') ? 'flagged' : '';
}
function empty(container, message) { container.append(el('p', message, 'muted empty-state')); }
// GROUP 4: API projections for the rail; local commands never enter agent findings.
function runMessages() { return state.messages.length ? state.messages : state.details?.messages || []; }
function findingPayload(name) {
  return runMessages().findLast((m) => m.type === 'FINDING' && m.sender === name)?.payload || {};
}
function clockLabel(stamp, milliseconds = false) {
  const time = Date.parse(stamp);
  return Number.isFinite(time) ? new Date(time).toISOString().slice(11, milliseconds ? 23 : 19) : '—';
}
function svgElement(tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}
function renderLatency() {
  const primary = state.report?.affected_devices?.[0] || findingPayload('correlator').diagnosis?.affected_devices?.[0];
  const liveSeries = (findingPayload('normalizer').telemetry?.samples || [])
    .filter((s) => primary && s.device_id === primary).map((s) => ({ t: s.timestamp, value: s.latency_ms }));
  const series = (state.details?.latency_series ?? liveSeries)
    .filter((p) => Number.isFinite(p.value) && Number.isFinite(Date.parse(p.t)))
    .slice().sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
  const latest = series.at(-1)?.value;
  const evidence = state.report?.supporting_evidence || findingPayload('correlator').diagnosis?.evidence || [];
  const baseline = evidence.find((e) => e.metric === 'latency_ms' && e.device_id === primary && Number.isFinite(e.baseline))?.baseline;
  const anomaly = Number.isFinite(latest) && Number.isFinite(baseline) && baseline > 0 ? (latest - baseline) / baseline * 100 : null;
  $('latency-value').textContent = display(latest);
  $('latency-anomaly').textContent = anomaly === null ? '—' : (anomaly >= 0 ? '+' : '') + Math.round(anomaly) + '% Anomaly';
  $('latency-anomaly').className = anomaly !== null && anomaly > 0 ? 'alert' : 'muted';
  $('latency-first').textContent = clockLabel(series[0]?.t);
  $('latency-last').textContent = series.length > 1 ? clockLabel(series.at(-1).t) : '—';
  $('latency-labels').hidden = series.length < 2;
  $('latency-empty').hidden = series.length >= 2;
  $('latency-empty').textContent = series.length === 1 ? 'single sample' : 'No latency samples available.';
  const chart = $('latency-chart'); chart.replaceChildren(); chart.hidden = series.length < 2;
  if (series.length < 2) return;
  const svg = svgElement('svg', { viewBox: '0 0 266 90', role: 'img', 'aria-label': 'Latency trend for ' + display(primary) + ' (ms, UTC)' });
  const title = svgElement('title', {}); title.textContent = 'Latency (ms) · ' + display(primary); svg.append(title);
  [12, 36, 60, 84].forEach((y) => svg.append(svgElement('line', { x1: 0, y1: y, x2: 266, y2: y, class: 'chart-grid' })));
  [0, 66.5, 133, 199.5, 266].forEach((x) => svg.append(svgElement('line', { x1: x, y1: 6, x2: x, y2: 84, class: 'chart-grid' })));
  const max = Math.max(1, ...series.map((p) => p.value));
  const firstTime = Date.parse(series[0].t), span = Date.parse(series.at(-1).t) - firstTime;
  const points = series.map((p, i) => [span > 0 ? (Date.parse(p.t) - firstTime) / span * 266 : i / (series.length - 1) * 266,
    84 - p.value / max * 72]);
  const path = points.map(([x, y], i) => (i ? 'L' : 'M') + x.toFixed(2) + ',' + y.toFixed(2)).join(' ');
  svg.append(svgElement('path', { d: path + ' L266,84 L0,84 Z', class: 'latency-area' }),
    svgElement('path', { d: path, class: 'latency-line' }));
  chart.append(svg);
}
function renderRailStats() {
  const evidence = state.report?.supporting_evidence || [];
  const candidates = [
    ...(findingPayload('normalizer').telemetry?.samples || []).map((s) => ({ ...s, value: s.packet_loss_pct })),
    ...(state.details?.mappings || []).filter((m) => m.metric === 'packet_loss_pct'),
    ...(state.details?.devices || []).filter((d) => d.worst_metric === 'packet_loss_pct').map((d) => ({ ...d, value: d.worst_value })),
    ...runMessages().flatMap((m) => m.payload?.evidence || []).filter((e) => e.metric === 'packet_loss_pct'),
    ...evidence.filter((e) => e.metric === 'packet_loss_pct'),
  ].filter((e) => Number.isFinite(e.value));
  const worst = candidates.reduce((best, e) => !best || e.value > best.value ? e : best, null);
  const critical = worst && candidates.some((e) => e.device_id === worst.device_id && e.value === worst.value &&
    (e.severity === 'critical' || /(?:>=|<=) critical\b/.test(e.interpretation || '')));
  $('packet-loss').textContent = worst ? worst.value + '%' : '—';
  $('packet-loss').className = 'metric-value' + (critical ? ' alert' : '');
  $('packet-loss-device').textContent = display(worst?.device_id);
  $('rail-confidence').textContent = percent(state.report?.confidence ?? findingPayload('correlator').diagnosis?.confidence);
  const consensus = state.details?.consensus;
  $('rail-accord').textContent = consensus && Number.isFinite(consensus.in_accord) && Number.isFinite(consensus.total) ?
    consensus.in_accord + '/' + consensus.total + ' Agents in Accord' : '—';
}
function renderRightRail() { renderLatency(); renderRailStats(); }
const BUS_TYPES = new Set(['TASK', 'FINDING', 'QUERY', 'RESPONSE', 'ERROR']);
let renderedBusMessages = [];
function renderBus() {
  const list = $('bus-messages');
  const messages = [...runMessages(), ...state.consoleMessages]
    .sort((a, b) => (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0));
  // Keep existing log nodes so aria-live only announces newly arrived messages.
  let shared = 0;
  while (shared < messages.length && messages[shared] === renderedBusMessages[shared]) shared++;
  while (list.children.length > shared) list.lastChild.remove();
  messages.slice(shared).forEach((msg) => {
    const row = el('li', undefined, 'bus-message ' + (BUS_TYPES.has(msg.type) ? msg.type.toLowerCase() : 'task'));
    row.dataset.searchable = '';
    const heading = el('div', undefined, 'bus-message-heading');
    const time = el('time', clockLabel(msg.timestamp, true));
    if (Number.isFinite(Date.parse(msg.timestamp))) time.dateTime = msg.timestamp;
    time.title = display(msg.timestamp) + ' (UTC)';
    heading.append(el('span', '[' + display(msg.sender) + ' → ' + display(msg.recipient) + ']'), time);
    row.append(heading, el('p', '“' + (summarize(msg) || '—') + '”')); list.append(row);
  });
  const changed = messages.length !== renderedBusMessages.length || shared !== messages.length;
  renderedBusMessages = messages;
  $('bus-live').classList.toggle('live', state.running);
  $('bus-live').setAttribute('aria-label', state.running ? 'Bus live' : 'Bus idle');
  renderSearch();
  if (changed) list.scrollTop = list.scrollHeight;
}
function consoleMessage(sender, recipient, text, type = 'RESPONSE') {
  state.consoleMessages.push({ sender, recipient, type, timestamp: new Date().toISOString(), payload: { message: text } });
  renderBus();
}
async function executeCommand(command) {
  consoleMessage('You', 'Orchestrator', command, 'QUERY');
  const [verb, ...args] = command.split(/\s+/), argument = args.join(' ');
  const reply = (text, type) => consoleMessage('Orchestrator', 'You', text, type);
  try {
    switch (verb.toLowerCase()) {
      case 'help':
        reply('Local commands: help; run <scenario>; fail <agent> (next run; fail none clears); evidence; alts; remediate (simulated); export json.'); break;
      case 'run': {
        if (state.running) { reply('A diagnostic run is already running.'); break; }
        if (!state.scenariosReady) { reply('Scenarios are not available yet.'); break; }
        const option = [...$('scenario').options].find((o) => o.value === argument);
        if (!option) { reply('Use run <scenario>. Available: ' + [...$('scenario').options].map((o) => o.value).join(', ')); break; }
        $('scenario').value = option.value; $('file').value = '';
        await run(); reply($('status').textContent, state.runId ? 'RESPONSE' : 'ERROR'); break;
      }
      case 'fail': {
        const option = [...$('fail').options].find((o) => o.value === (argument === 'none' ? '' : argument));
        if (!argument || !option) { reply('Use fail <agent>. Available: ' + [...$('fail').options].map((o) => o.value || 'none').join(', ')); break; }
        $('fail').value = option.value;
        reply(option.value ? 'Fault injection selected for next run: ' + option.value : 'Fault injection cleared for next run.'); break;
      }
      case 'evidence': {
        const evidence = state.report?.supporting_evidence;
        reply(evidence?.length ? evidence.map((e) => display(e.device_id) + ': ' + metricText(e.metric, e.value, e.unit) + ' — ' + display(e.interpretation)).join('\n') : 'No supporting evidence available.'); break;
      }
      case 'alts': {
        const alternatives = state.details?.alternatives;
        reply(alternatives?.length ? alternatives.map((a) => display(a.cause) + ': ' + percent(a.confidence) + ' — ' + display(a.rationale)).join('\n') :
          state.report?.alternative_causes?.join('\n') || 'No alternative explanations reported.'); break;
      }
      case 'remediate':
        if (!canRemediate()) { reply('Run a supported diagnosis before simulated remediation.'); break; }
        window.openRemediateModal(); reply('Simulated remediation confirmation opened.'); break;
      case 'export':
        if (argument !== 'json') { reply('Use export json.'); break; }
        if (!state.report || state.running) { reply('No completed report available to export.'); break; }
        await downloadReport('json'); reply('JSON report downloaded for run ' + state.runId + '.'); break;
      default: reply('Unknown local command. Type help for available commands.');
    }
  } catch (err) { reply('Command error: ' + err.message, 'ERROR'); }
}
$('bus-console').addEventListener('submit', (event) => {
  event.preventDefault();
  const command = $('bus-input').value.trim();
  if (!command) return;
  $('bus-input').value = ''; void executeCommand(command);
});
function renderReport() {
  const r = state.report, d = state.details;
  $('report').hidden = false;
  $('report').classList.toggle('is-idle', !r && !state.running && !state.runId);
  $('report').setAttribute('aria-busy', String(state.running));
  $('report-state').textContent = !r ? state.running ? 'Agents correlating…' : state.runId ? 'No report available.' : '' : '';
  $('report-state').hidden = !!r || (!state.running && !state.runId);
  $('report-content').hidden = !r && (state.running || !!state.runId);
  $('correlation-hash').textContent = 'CORRELATION HASH: ' + (r?.run_id ? '#' + r.run_id : '—');
  const formats = d?.formats || [], mappings = d?.mappings || [];
  const availableFormats = [...new Set([...formats, ...mappings.map((m) => m.source_format)].filter(Boolean))];
  $('format-normalization').textContent = 'Format Normalization: ' + (availableFormats.length ?
    availableFormats.length + ' conflicting formats (' + availableFormats.join(', ') + ')' : '—');
  renderRootCause(); renderAffectedDevices(); renderEvidence(); renderTelemetry(); renderHypotheses(); renderActions();
  renderSearch();
}
function renderRootCause() {
  const r = state.report, w = state.details?.window, box = $('summary');
  box.replaceChildren();
  const exceptional = r && ['insufficient_evidence', 'error'].includes(r.status);
  box.className = 'root-cause' + (exceptional ? r.status === 'error' ? ' error' : ' insufficient' : '');
  const label = el('p', exceptional ? display(r.status).replaceAll('_', ' ').toUpperCase() : 'SYNTHESIZED PROBABLE ROOT CAUSE', 'mono callout-label');
  label.append(el('strong', r?.cause ? ' / ' + r.cause.replaceAll('_', ' ') : ''));
  const cause = exceptional ? (r.limitations?.join('; ') || '—') : r?.suspected_root_cause;
  box.append(label, el('p', cause ? '“' + cause + '”' : '—', 'root-cause-text'));
  if (!exceptional) box.append(el('p', r?.affected_devices?.length ? 'Affected devices: ' + r.affected_devices.join(', ') : '—', 'affected-sentence'));
  const meta = el('div', undefined, 'callout-meta');
  meta.append(el('span', 'Trigger Window: ' + display(w?.first) + ' – ' + display(w?.last)),
    el('span', 'Multi-Agent Consensus: ' + percent(r?.confidence) + ' ' + display(r?.confidence_category) + ' Confidence'));
  box.append(meta);
  if (!exceptional && r?.limitations?.length) box.append(el('p', 'Limitations: ' + r.limitations.join('; '), 'muted report-limitations'));
}
function renderAffectedDevices() {
  const grid = $('affected-devices'), ids = state.report?.affected_devices || [];
  grid.replaceChildren();
  ids.slice(0, 3).forEach((id) => {
    const d = state.details?.devices?.find((device) => device.device_id === id);
    const card = el('article', undefined, 'device-card');
    card.append(el('span', display(d?.role || d?.type), 'mono stat-label'), el('strong', id),
      el('p', [d?.location, d?.type].filter(Boolean).join(' · ') || '—', 'device-description'));
    card.append(el('span', d?.worst_metric ? metricText(d.worst_metric, d.worst_value, d.unit) : '—',
      'pill ' + (d?.severity === 'critical' ? 'alert' : d?.severity === 'warning' ? 'flagged' : '')));
    grid.append(card);
  });
  if (!ids.length) empty(grid, state.report ? 'No affected devices identified.' : '—');
  if (ids.length > 3) grid.append(el('p', '+' + (ids.length - 3) + ' more', 'muted more-devices'));
}
function renderEvidence() {
  const grid = $('evidence'), rows = state.report?.supporting_evidence || [];
  grid.replaceChildren();
  const seconds = state.details?.window?.seconds;
  $('sample-window').textContent = Number.isFinite(seconds) && seconds > 0 ?
    'SAMPLED OVER ' + seconds + ' SECONDS' : seconds === 0 ? 'SINGLE SAMPLE' : '—';
  rows.slice(0, 5).forEach((e) => {
    const tile = el('article', undefined, 'evidence-tile');
    tile.dataset.searchable = '';
    tile.append(el('span', display(e.metric).replaceAll('_', ' '), 'mono stat-label'),
      el('strong', display(e.value) + (e.unit ? ' ' + e.unit : ''), 'metric-value ' + evidenceTone(e)),
      el('span', e.baseline != null ? 'Baseline: ' + e.baseline + (e.unit ? ' ' + e.unit : '') :
        e.threshold != null ? 'Threshold: ' + e.threshold + (e.unit ? ' ' + e.unit : '') : '—', 'mono evidence-detail'),
      el('span', display(e.device_id), 'mono evidence-detail'));
    tile.title = e.interpretation || ''; grid.append(tile);
  });
  if (!rows.length) empty(grid, state.report ? 'No supporting evidence available.' : '—');
}
function renderTelemetry() {
  const body = $('telemetry-rows'), affected = new Set(state.report?.affected_devices || []);
  const rows = (state.details?.mappings || []).filter((m) => affected.has(m.device_id));
  body.replaceChildren();
  rows.forEach((m) => {
    const row = el('tr'); row.dataset.searchable = '';
    row.append(el('td', display(m.source_format), 'telemetry-feed'), el('td', display(m.device_id)),
      el('td', display(m.raw_path) + ' = ' + display(m.raw_value), 'raw-fragment'),
      el('td', metricText(m.metric, m.value, m.unit), evidenceTone(m)));
    row.title = m.note || ''; body.append(row);
  });
  $('telemetry-empty').textContent = rows.length ? '' : state.report ? 'No normalization mappings for affected devices.' : '—';
  $('telemetry-empty').hidden = !!rows.length;
}
function confidenceBar(value) {
  const bar = el('div', undefined, 'confidence-track');
  bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-label', 'Hypothesis confidence');
  bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
  const fill = el('span');
  if (Number.isFinite(value)) { const p = Math.max(0, Math.min(100, value * 100)); fill.style.width = p + '%'; bar.setAttribute('aria-valuenow', String(p)); }
  else { fill.style.width = '0%'; bar.setAttribute('aria-valuetext', 'Unavailable'); }
  bar.append(fill); return bar;
}
function renderHypotheses() {
  const r = state.report, d = state.details, primary = $('primary-hypothesis'), grid = $('alts');
  primary.replaceChildren(); grid.replaceChildren();
  primary.append(el('p', 'Primary Hypothesis: ' + display(r?.cause) + ' on ' + (r?.affected_devices?.join(', ') || '—') +
    ' — ' + percent(r?.confidence) + ' (' + display(r?.confidence_category) + ')', 'primary-title mono'),
    confidenceBar(r?.confidence), el('p', d?.primary_rationale || '—', 'hypothesis-rationale'));
  const alternatives = d?.alternatives?.length ? d.alternatives : (r?.alternative_causes || []).map((cause) => ({ cause }));
  alternatives.forEach((a) => {
    const known = Number.isFinite(a.confidence), ruledOut = known && a.confidence < 0.2;
    const card = el('article', undefined, 'alternative-card' + (known ? ruledOut ? ' ruled-out' : ' partial' : ''));
    const heading = el('div', undefined, 'alternative-heading');
    heading.append(el('strong', display(a.cause)), el('span', percent(a.confidence) + ' Probability', 'mono'));
    const rationale = el('p', undefined, 'hypothesis-rationale');
    rationale.append(el('strong', known ? ruledOut ? 'Ruled Out: ' : 'Partial Contributor: ' : '— '), el('span', a.rationale || '—'));
    card.append(heading, confidenceBar(a.confidence), rationale); grid.append(card);
  });
  if (!alternatives.length) empty(grid, r ? 'No alternative explanations reported.' : '—');
}
function canRemediate() {
  return !!state.runId && !state.running && !state.remediating && state.report?.status === 'diagnosed' && REMEDIABLE_CAUSES.has(state.report?.cause);
}
function renderActions() {
  const list = $('actions'); list.replaceChildren();
  (state.report?.recommended_actions || []).forEach((action, i) => {
    const row = el('li', undefined, 'action-row'), copy = el('div', undefined, 'action-copy');
    // The API provides a single runbook string, so retain it intact without invented descriptions.
    copy.append(el('strong', action), el('span', i === 0 ? 'Simulated playbook execution' : 'Runbook step', 'muted'));
    const button = el('button', i === 0 ? 'Apply Simulated Remediation' : 'Copy Runbook Step', 'button ' + (i === 0 ? 'primary' : 'outline'));
    if (i === 0) { button.id = 'fix'; button.disabled = !canRemediate(); button.onclick = window.openRemediateModal; }
    else button.onclick = async () => {
      try { await navigator.clipboard.writeText(action); button.textContent = 'Copied'; }
      catch { $('fixresult').textContent = 'Clipboard unavailable. Select and copy the runbook step.'; }
    };
    row.append(el('span', String(i + 1), 'action-number mono'), copy, button); list.append(row);
  });
  if (!list.children.length) empty(list, state.report ? 'No corrective actions reported.' : '—');
  const result = state.remediation;
  $('fixresult').textContent = result ? result.error ? 'Simulated remediation error: ' + result.error :
    'Simulated remediation: ' + display(result.before) + ' → ' + display(result.after) + ' — verified ' + (result.verified ? '✓' : '✗') : '';
  $('fixresult').className = result ? 'remediation-result ' + (result.verified ? 'pass' : 'fail') : '';
}
window.openRemediateModal = () => {
  if (!canRemediate()) { $('status').textContent = 'Run a supported diagnosis to approve simulated remediation.'; return; }
  $('remediate-target').textContent = 'Run: ' + state.runId + ' · Cause: ' + display(state.report.cause);
  $('remediate-modal').dataset.runId = state.runId;
  if (!$('remediate-modal').open) $('remediate-modal').showModal();
  $('remediate-cancel').focus();
};
// Remediation transport stays alongside the single run/report data-loading layer.
async function remediateRun(runId) { return json('/api/runs/' + runId + '/remediate', { method: 'POST' }); }
$('remediate-cancel').onclick = () => $('remediate-modal').close();
$('remediate-confirm').onclick = async () => {
  const runId = $('remediate-modal').dataset.runId;
  if (runId !== state.runId || !canRemediate()) { $('remediate-modal').close(); return; }
  state.remediating = true; $('remediate-modal').close(); renderActions();
  try { const result = await remediateRun(runId); if (runId === state.runId) state.remediation = result; }
  catch (err) { if (runId === state.runId) state.remediation = { error: err.message }; }
  finally { state.remediating = false; renderActions(); await refreshShellData(); }
};

async function run() {
  if (state.running) return;
  state.running = true;
  closeExportMenu();
  $('run').disabled = true; $('status').textContent = 'Starting…';
  state.remediation = null; $('remediate-modal').close();
  state.runId = null; state.report = null; state.details = null; state.topology = null; state.messages = [];
  if (cy) { cy.destroy(); cy = null; }
  $('topo').textContent = '';
  setView('console'); renderShell();
  if (source) source.close();
  try {
    const body = { fail_agent: $('fail').value || null };
    const f = $('file').files[0];
    if (f) body.upload_id = (await json('/api/upload', { method: 'POST', body: await f.text() })).upload_id;
    else body.scenario = $('scenario').value || 'congestion';
    state.runId = (await json('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) })).run_id;
    $('status').textContent = 'Run ' + state.runId;
    renderShell(); subscribeRun();
  } catch (err) {
    $('status').textContent = 'Error: ' + err.message;
    state.running = false; $('run').disabled = false; renderShell();
  }
}

function closeExportMenu() {
  $('export-menu').hidden = true;
  $('export-toggle').setAttribute('aria-expanded', 'false');
}
// Export transport belongs to the same data-loading layer as run/report fetching.
async function downloadReport(format) {
  if (!state.report || !state.runId || state.running) return;
  if (format === 'pdf') { window.print(); return; }
  const runId = state.runId;
  const url = `/api/runs/${runId}/report` + (format === 'md' ? '.md' : '');
  let blob;
  if (format === 'json') blob = new Blob([JSON.stringify(await json(url), null, 2)], { type: 'application/json' });
  else {
    const response = await fetch(url);
    if (!response.ok) throw new Error(response.statusText);
    blob = await response.blob();
  }
  const link = el('a');
  link.href = URL.createObjectURL(blob); link.download = 'netcoop-' + runId + '.' + format;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
$('trigger-run').onclick = run;
$('export-toggle').onclick = () => {
  const open = $('export-menu').hidden;
  $('export-menu').hidden = !open;
  $('export-toggle').setAttribute('aria-expanded', String(open));
  if (open) $('export-menu').querySelector('button').focus();
};
document.querySelectorAll('[data-export]').forEach((button) => {
  button.onclick = async () => {
    closeExportMenu(); $('export-toggle').focus();
    try { await downloadReport(button.dataset.export); }
    catch (err) { $('status').textContent = 'Export error: ' + err.message; }
  };
});
document.addEventListener('click', (event) => { if (!event.target.closest('.export-control')) closeExportMenu(); });
// Keep relative sweep time current without refetching the finished run.
setInterval(() => { if (state.details?.finished_at) renderHero(); }, 1000);
$('run').onclick = run;
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
  if (event.key === 'Escape' && !$('export-menu').hidden) { closeExportMenu(); $('export-toggle').focus(); }
  if (event.key === 'Escape') { $('sidebar').classList.remove('open'); $('sidebar-toggle').setAttribute('aria-expanded', 'false'); }
});
$('spawn-run').onclick = () => {
  if (typeof window.openRunModal === 'function') { window.openRunModal(); return; }
  setView('console'); $('run-controls').scrollIntoView({ block: 'start' }); $('scenario').focus();
};
$('quarantine').onclick = () => window.openRemediateModal();
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
