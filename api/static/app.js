const $ = (id) => document.getElementById(id);
// All API data lives here; renderers never fetch. cy/source are disposable UI resources.
const state = {
  runId: null, health: null, runs: null, report: null, details: null,
  messages: [], consoleMessages: [], topology: null, view: 'console', search: '', acknowledged: readAcknowledged(),
  running: false, scenariosReady: false, failAgent: null, remediation: null, remediating: false, telemetryExpanded: false,
  playbooks: null, playbooksError: '', backendPending: 0, backendFailed: false, error: '', samplePage: 0,
};
let cy = null, source = null, recoveryTimer = null;
const NO_SAMPLES = [];
let renderedSamples = null;
let renderedSamplePage = -1;
const ACK_KEY = 'netcoop.acknowledged';
// Presentation labels only; identifiers and API payloads retain their original values.
const LABELS = {
  normal: 'Normal', congestion: 'Congestion', weak_wifi: 'Weak Wi-Fi',
  interface_fault: 'Interface fault', device_overload: 'Device overload',
  dns_failure: 'DNS failure', dhcp_failure: 'DHCP failure', hardware_failure: 'Hardware failure',
  latency_degradation: 'Latency degradation', insufficient_evidence: 'Insufficient evidence',
  error: 'Input rejected', diagnosed: 'Diagnosed',
  link_utilization_pct: 'Link Utilization', latency_ms: 'Round-Trip Latency',
  queue_drops: 'Queue Drops', packet_loss_pct: 'Packet Loss Rate', jitter_ms: 'Jitter',
  cpu_pct: 'CPU', mem_pct: 'Memory', interface_errors: 'Interface Errors',
  rssi_dbm: 'Wi-Fi RSSI', retransmission_pct: 'Retransmissions',
  dns_status: 'DNS Status', dns_success_rate_pct: 'DNS Success Rate', dns_response_ms: 'DNS Response Time',
  dhcp_status: 'DHCP Status', dhcp_success_rate_pct: 'DHCP Success Rate', dhcp_response_ms: 'DHCP Response Time',
  status: 'Status', bandwidth_mbps: 'Bandwidth', throughput_mbps: 'Throughput',
  capacity_mbps: 'Link Capacity', success_rate_pct: 'Success Rate', response_ms: 'Response Time',
  high: 'High', medium: 'Medium', low: 'Low', insufficient: 'Insufficient',
  ap: 'Wi-Fi AP', router: 'Router', switch: 'Switch', server: 'Server', host: 'Host',
};
const AGENT_NAMES = {
  normalizer: 'NormalizerAgent', performance: 'PerformanceAgent', infrastructure: 'InfrastructureAgent',
  connectivity: 'ConnectivityAgent', correlator: 'CorrelatorAgent', reporter: 'ReporterAgent',
  orchestrator: 'Orchestrator', You: 'You', Orchestrator: 'Orchestrator',
};
function label(value) {
  if (value == null || value === '') return '—';
  return Object.hasOwn(LABELS, value) ? LABELS[value] : humanText(value).replaceAll('_', ' ');
}
function counted(value, singular, plural = singular + (singular === singular.toUpperCase() ? 'S' : 's')) {
  return Number.isFinite(value) ? value + ' ' + (value === 1 ? singular : plural) : '—';
}
function humanText(value) {
  // API prose can embed enum names and backend-generated counts. Do not alter device IDs or paths.
  return String(value ?? '').replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (word) =>
    Object.hasOwn(LABELS, word) ? LABELS[word] : word)
    .replace(/\b(Top hypothesis:?|Concluded) (normal|congestion)\b/g, (_, prefix, cause) => prefix + ' ' + LABELS[cause])
    .replace(/\b(\d+) (devices?|samples?|fields?|formats?|records?|findings?|agents?|seconds?|rounds?|nodes?|links?|issues?|sources?)\b(?:\(s\))?/gi,
      (_, n, noun) => counted(Number(n), noun.replace(/s$/i, '')))
    .replace(/\b1 node are\b/g, '1 node is');
}
function agentName(name) { return AGENT_NAMES[name] || label(name); }
function roleLabel(device) {
  if (!device) return '—';
  const role = device.role ? label(device.role) : '';
  const type = device.type ? label(device.type) : '';
  return (role && type && !role.toLowerCase().includes(type.toLowerCase()) ? role + ' ' + type : role || type || '—').toUpperCase();
}
function unitSuffix(unit) { return unit && unit !== 'count' ? ' ' + unit : ''; }

function readAcknowledged() {
  try {
    const saved = JSON.parse(localStorage.getItem('netcoop.acknowledged') || '[]');
    return new Set(Array.isArray(saved) ? saved.filter((id) => typeof id === 'string') : []);
  } catch { return new Set(); }
}
function incidentKey(run) { return run.run_id; }
function isFault(report) {
  return report?.status === 'diagnosed' && !!report.cause &&
    !['normal', 'insufficient_data', 'insufficient_evidence', 'error', 'unknown'].includes(report.cause);
}
function renderTopBar() {
  $('campus-subtitle').textContent = 'Campus Core Grid • ' + (state.report?.incident_id || 'idle');
}
function renderSidebar() {
  const critical = state.runs?.filter((r) => isFault(r) && !r.recovered && r.confidence >= 0.75 &&
    !state.acknowledged.has(incidentKey(r)));
  $('critical-badge').hidden = !critical?.length;
  $('critical-badge').textContent = critical ? critical.length + '\nCRIT' : '—';
  $('api-host').textContent = 'HOST: ' + (state.health ? location.host : '—');
  $('api-uptime').textContent = 'UP: ' + (Number.isFinite(state.health?.uptime_s) ? state.health.uptime_s + 's' : '—');
}
const PIPELINE_AGENTS = [
  ['normalizer', 'INGESTION'], ['performance', 'PERFORMANCE'],
  ['infrastructure', 'INFRASTRUCTURE'], ['connectivity', 'TOPOLOGY'], ['correlator', 'RCA'], ['reporter', 'REPORT'],
];
const AGENT_STATUSES = new Set(['pending', 'running', 'complete', 'flagged', 'alert', 'failed', 'abstained', 'synthesizing', 'skipped']);
function inputRejected() {
  return (state.report?.status ?? state.details?.status) === 'error' ||
    runMessages().some((m) => m.sender === 'normalizer' && m.type === 'ERROR');
}
function votingConsensus() {
  if (inputRejected()) return null;
  const votes = ['performance', 'infrastructure', 'connectivity'].flatMap((name) => {
    const agent = state.details?.agents?.find((a) => a.name === name);
    if (agent) return agent.status !== 'failed' && agent.top_hypothesis ? [agent.top_hypothesis] : [];
    if (liveAgent(name).status === 'failed') return [];
    const top = [...(findingPayload(name).hypotheses || [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    return top?.cause ? [top.cause] : [];
  });
  if (!votes.length) return null;
  const counts = new Map();
  votes.forEach((cause) => counts.set(cause, (counts.get(cause) || 0) + 1));
  const k = Math.max(...counts.values()), n = votes.length;
  return { k, n, label: k === n ? 'SYNCHRONIZED' : k > n / 2 ? 'RESOLVED' : 'DIVERGENT' };
}
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
  const normalWarning = r?.status === 'diagnosed' && r.cause === 'normal' &&
    (r.confidence < 0.75 || (r.limitations || []).some((item) => item.startsWith('warning-grade devices')));
  const recovered = (r?.limitations || []).some((item) => item.startsWith('historical: '));
  let health = '—', tone = '';
  if (inputRejected()) { health = 'Unknown — input rejected'; tone = 'muted'; }
  else if (recovered) { health = 'Recovered — historical incident'; tone = 'flagged'; }
  else if (r?.status === 'diagnosed' && r.cause === 'normal') {
    health = normalWarning ? 'Normal with warnings' : 'Healthy';
    tone = normalWarning ? 'flagged' : 'complete';
  }
  else if (isFault(r) && r.confidence >= 0.75) {
    health = 'Critical'; tone = 'alert';
  } else if (isFault(r)) { health = 'Degraded'; tone = 'flagged'; }
  else if (r) { health = 'Unknown'; tone = 'muted'; }
  $('mesh-health').textContent = health === '—' ? health : 'Campus Mesh: ' + health;
  $('mesh-health').className = tone;
  $('mesh-health-dot').className = 'health-dot ' + tone;
  // Abstaining agents still produced a finding; unavailable agents did not.
  const produced = new Set(state.messages.filter((m) => m.type === 'FINDING').map((m) => m.sender));
  const agents = d?.agents;
  const count = state.messages.length ? PIPELINE_AGENTS.filter(([name]) => produced.has(name)).length :
    agents ? agents.filter((a) => a.status !== 'failed' &&
      (a.name !== 'correlator' || a.top_hypothesis != null)).length : null;
  $('swarm-status').textContent = count === null ? '—' : `Swarm Status: ${counted(count, 'Active Diagnostic Agent')}`;
  $('swarm-status').className = count === null ? '' : 'complete';
  $('swarm-status-dot').className = 'health-dot' + (count === null ? '' : ' complete');
  const since = relativeTime(d?.finished_at);
  $('coordination-cycle').textContent = since === '—' ? '—' : 'Last Coordinated Sweep: ' + since;
  $('query-rounds').textContent = counted(d?.rounds, 'query round');
  $('active-incident').textContent = !inputRejected() && r?.incident_id ? 'Active Incident: ' + r.incident_id : '—';
  const severity = $('incident-severity');
  severity.hidden = !r || inputRejected();
  const high = isFault(r) && r.confidence >= 0.75;
  const medium = r?.confidence_category === 'medium';
  const low = ['low', 'insufficient'].includes(r?.confidence_category);
  const unclassified = r && !isFault(r) && r.cause !== 'normal';
  severity.textContent = recovered ? 'Resolved' : normalWarning ? 'Watch' : unclassified ? 'Unclassified' : high ? 'P1 CRIT' : medium ? 'P2' : low ? 'P3' : '—';
  severity.className = 'pill ' + (recovered ? 'flagged' : high ? 'alert' : normalWarning || medium ? 'flagged' : '');
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
      view.stats = { Ingested: Array.isArray(p.telemetry?.samples) ? counted(p.telemetry.samples.length, 'sample') : '—',
        Mapped: Array.isArray(p.mappings) ? counted(p.mappings.length, 'field') : '—' };
    } else if (name === 'reporter') {
      view.status = p.report ? 'complete' : 'abstained';
      view.summary = p.report ? 'Unified report assembled' : 'No report produced';
    } else if (name === 'correlator') {
      const diagnosis = p.diagnosis || {};
      view.status = diagnosis.cause && diagnosis.cause !== 'normal' ?
        diagnosis.status === 'diagnosed' && diagnosis.confidence >= 0.75 ? 'alert' : 'flagged' : 'complete';
      view.summary = diagnosis.cause ? 'Top hypothesis: ' + label(diagnosis.cause) : '';
      view.stats = { Confidence: Number.isFinite(diagnosis.confidence) ? Math.round(diagnosis.confidence * 100) + '%' : '—',
        Conflicts: Array.isArray(diagnosis.conflicts) ? diagnosis.conflicts.length : '—' };
    } else {
      const top = [...(p.hypotheses || [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
      // Critical classifications without an explicit SSE severity arrive via /details.
      const critical = p.evidence?.some((e) => e.severity === 'critical' || /(?:>=|<=) critical\b/.test(e.interpretation || ''));
      view.status = !top ? 'abstained' : top.cause === 'normal' ? 'complete' : critical ? 'alert' : 'flagged';
      view.summary = [top?.cause ? 'Top hypothesis: ' + label(top.cause) +
        (Number.isFinite(top.score) ? ' (' + Math.round(top.score * 100) + '%)' : '') : '', ...(p.notes || []).slice(0, 1)].filter(Boolean).join('. ');
      view.stats = { Evidence: Array.isArray(p.evidence) ? p.evidence.length : '—',
        Flagged: Array.isArray(p.affected_devices) ? counted(p.affected_devices.length, 'device') : '—' };
    }
    view.findingStatus = view.status;
  }
  return view;
}
function renderPipeline() {
  const agreement = votingConsensus();
  $('pipeline-consensus').textContent = inputRejected() ? 'HALTED (input rejected)' : agreement ?
    `${agreement.label} (${agreement.k}/${counted(agreement.n, 'AGENT')} ENGAGED)` : '—';
  $('pipeline-consensus').className = agreement ? agreement.label.toLowerCase() : '';
  const grid = $('pipeline-agents');
  grid.classList.toggle('is-idle', !state.runId && !state.running);
  grid.replaceChildren();
  PIPELINE_AGENTS.forEach(([name, category], i) => {
    const agent = inputRejected() && name !== 'normalizer' ?
      { status: 'skipped', summary: 'Input rejected by normalizer', stats: {} } :
      state.details?.agents?.find((a) => a.name === name) || liveAgent(name);
    const status = AGENT_STATUSES.has(agent.status) ? agent.status : 'pending';
    const card = el('article', undefined, 'agent-card ' + status);
    card.id = 'agent-' + name;
    const header = el('div', undefined, 'agent-heading');
    header.append(el('span', '// AGENT-' + String(i + 1).padStart(2, '0') + ':', 'mono agent-index'),
      el('span', status[0].toUpperCase() + status.slice(1), 'pill ' + status));
    const title = el('h3', name[0].toUpperCase() + name.slice(1) + ' Agent');
    const summary = el('p', humanText(agent.summary) || '—', 'agent-summary');
    summary.title = humanText(agent.summary);
    const footer = el('div', undefined, 'agent-stats mono');
    const stats = Object.entries(agent.stats || {}).slice(0, 2);
    for (let j = 0; j < 2; j++) {
      const stat = el('span');
      if (stats[j]) stat.append(el('span', stats[j][0] + ': ', 'muted'), el('strong', humanText(stats[j][1] ?? '—')));
      else stat.textContent = '—';
      footer.append(stat);
    }
    card.append(header, el('p', category, 'agent-category mono'), title, summary, footer);
    grid.append(card);
  });
}
function renderViews() {
  // Move shared sections rather than cloning ids, subscriptions or command forms.
  const pipelineParent = state.view === 'pipeline' ? $('pipeline-slot') : $('view-console');
  if ($('pipeline').parentElement !== pipelineParent) {
    if (state.view === 'pipeline') pipelineParent.append($('pipeline'));
    else pipelineParent.insertBefore($('pipeline'), $('report-layout'));
  }
  const busParent = state.view === 'telemetry' ? $('telemetry-bus-slot') :
    state.view === 'terminal' ? $('terminal-bus-slot') : $('right-rail');
  if ($('agent-bus').parentElement !== busParent) busParent.append($('agent-bus'));
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
  if (view === 'topology') { drawTopology(); if (cy) { cy.resize(); cy.fit(); } }
  if (view === 'incidents') void refreshRuns();
  if (view === 'playbooks' && !state.playbooks) void loadPlaybooks();
  if (view === 'terminal') $('bus-input').focus();
}
function renderSearch() {
  document.querySelectorAll('#evidence > li, [data-searchable]').forEach((row) => {
    row.hidden = !row.textContent.toLowerCase().includes(state.search);
  });
  const visible = [...$('bus-messages').children].some((row) => !row.hidden);
  $('bus-empty').hidden = visible;
  $('bus-empty').textContent = state.search ? 'No matching messages.' : state.running ? 'Waiting for run messages…' : 'No run messages yet.';
}
function renderShell() {
  renderTopBar(); renderSidebar(); renderHero(); renderPipeline(); renderReport(); renderRightRail(); renderBus();
  renderIncidents(); renderAgentMessages(); renderSamples(); renderDevices(); renderInfo(); renderStates();
}

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function json(url, opts) {
  const watchBackend = url === '/api/health' || url === '/api/run' || url === '/api/upload';
  const controller = new AbortController();
  let slow = false;
  const timer = watchBackend ? setTimeout(() => {
    slow = true; state.backendPending++; renderStates();
  }, 1500) : null;
  const timeout = setTimeout(() => controller.abort(), 60000);
  try {
    const r = await fetch(url, { ...opts, signal: controller.signal });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      // Validation/rate-limit errors are not backend availability failures.
      if (watchBackend && r.status >= 500) state.backendFailed = true;
      const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      const err = new Error(detail || r.statusText);
      err.status = r.status;
      throw err;
    }
    if (watchBackend) state.backendFailed = false;
    return body;
  } catch (err) {
    if (watchBackend && (err.name === 'AbortError' || err instanceof TypeError)) state.backendFailed = true;
    if (err.name === 'AbortError') throw new Error('Backend request timed out. Retry connection.');
    throw err;
  } finally {
    clearTimeout(timer); clearTimeout(timeout);
    if (slow) state.backendPending--;
    renderStates();
  }
}

async function init() {
  const results = await Promise.allSettled([json('/api/scenarios'), refreshShellData()]);
  if (results[0].status === 'rejected') throw results[0].reason;
  const list = results[0].value;
  const selected = $('scenario').value || 'congestion';
  $('scenario').replaceChildren();
  list.forEach((s) => {
    const o = el('option', label(s.name) + '  (' + label(s.expected) + ')'); o.value = s.name; $('scenario').append(o);
  });
  $('scenario').value = list.some((s) => s.name === selected) ? selected : list[0]?.name || '';
  state.scenariosReady = list.length > 0; renderHero(); renderStates();
  // `?run=<id>` opens a finished run directly (shareable report links, README screenshots).
  const deepRun = new URLSearchParams(location.search).get('run');
  if (deepRun && /^[\w-]{1,64}$/.test(deepRun)) selectRun({ run_id: deepRun });
}

// Single data-loading/subscription layer: all endpoint calls stay in these helpers.
async function refreshShellData() {
  const results = await Promise.allSettled([json('/api/health'), json('/api/runs')]);
  if (results[0].status === 'fulfilled') state.health = results[0].value;
  if (results[1].status === 'fulfilled') state.runs = results[1].value;
  renderShell();
}
async function refreshRuns() {
  try { state.runs = await json('/api/runs'); renderSidebar(); renderIncidents(); }
  catch (err) { state.error = 'Cannot load incidents: ' + err.message; renderStates(); }
}
async function loadPlaybooks() {
  try { state.playbooks = await json('/api/playbook'); state.playbooksError = ''; }
  catch (err) { state.playbooksError = err.message; }
  renderPlaybooks();
}
async function loadRunData(runId = state.runId) {
  const report = await json(`/api/runs/${runId}/report`);
  if (state.runId !== runId) return;
  state.report = report;
  const results = await Promise.allSettled([
    json(`/api/runs/${runId}/details`), json(`/api/runs/${runId}/topology`), refreshShellData(),
  ]);
  if (state.runId !== runId) return;
  state.details = results[0].status === 'fulfilled' ? results[0].value : null;
  state.topology = results[1].status === 'fulfilled' ? results[1].value : null;
  renderShell(); drawTopology(); renderSearch();
}
function subscribeRun(attempt = 0, seen = new Set()) {
  const runId = state.runId;
  const stream = new EventSource(`/api/runs/${runId}/stream`);
  source = stream;
  const current = () => source === stream && state.runId === runId;
  const missingRun = (err) => {
    if (err.status !== 404 || !current()) return false;
    stream.close(); clearTimeout(recoveryTimer); source = null;
    state.running = false; state.runId = null;
    state.error = 'Run not found - it may have expired or belongs to another browser session.';
    $('status').textContent = '';
    renderShell(); renderStates(); void refreshRuns();
    return true;
  };
  const complete = async () => {
    if (!current()) return;
    try {
      await loadRunData(runId);
      if (!current()) return;
      state.running = false; state.error = '';
      $('status').textContent = 'Run ' + runId + ' complete';
      renderShell();
    } catch (err) {
      if (!current() || missingRun(err)) return;
      $('status').textContent = 'Waiting for report; retrying connection...';
      renderStates();
      recoveryTimer = setTimeout(complete, 5000);
    }
  };
  stream.onmessage = (e) => {
    if (!current()) return;
    try {
      const msg = JSON.parse(e.data);
      // Reconnected streams replay the log from the beginning.
      const key = msg.msg_id || JSON.stringify(msg);
      if (!seen.has(key)) { seen.add(key); addMessage(msg); }
    } catch { state.error = 'Error: unreadable stream message'; renderStates(); }
  };
  stream.addEventListener('done', () => {
    stream.close();
    if (current()) void complete();
  });
  stream.onerror = async () => {
    stream.close();
    if (!current()) return;
    try {
      await json(`/api/runs/${runId}/report`);
      if (!current()) return;
      await complete();
      return;
    } catch (err) {
      if (!current() || missingRun(err)) return;
    }
    $('status').textContent = attempt < 3 ? 'Stream interrupted; reconnecting...' : 'Waiting for report...';
    renderStates();
    recoveryTimer = setTimeout(() => {
      if (!current()) return;
      if (attempt < 3) subscribeRun(attempt + 1, seen);
      else void complete();
    }, 1000 * 2 ** attempt);
  };
}

function summarize(msg) {
  const p = msg.payload || {};
  if (msg.type === 'TASK') {
    if (['performance', 'infrastructure', 'connectivity'].includes(msg.recipient)) {
      return 'analyze ' + (Array.isArray(p.telemetry?.devices) ? counted(p.telemetry.devices.length, 'device') : counted(p.telemetry?.devices, 'device')) +
        ' / ' + (Array.isArray(p.telemetry?.samples) ? counted(p.telemetry.samples.length, 'sample') : counted(p.telemetry?.samples, 'sample'));
    }
    if (msg.recipient === 'correlator') {
      const count = Array.isArray(p.findings) ? p.findings.length :
        p.findings && typeof p.findings === 'object' ? Object.keys(p.findings).length : null;
      return 'correlate ' + counted(count, 'finding') + ' (round ' + display(p.round) + ')';
    }
    if (msg.recipient === 'reporter') return 'assemble unified report';
  }
  if (msg.type === 'FINDING' && msg.sender === 'normalizer') {
    return 'normalized ' + (p.formats?.length ? p.formats.join(', ') : '—') +
      ', data quality ' + percent(p.data_quality?.score);
  }
  if (p.message) return p.rawText ? p.message : humanText(p.message);
  if (p.action) return [label(p.action), humanText(p.reason)].filter(Boolean).join(': ');
  if (p.query_type || p.type) return label(p.query_type || p.type);
  if (p.diagnosis && p.diagnosis.cause) return 'diagnosis: ' + label(p.diagnosis.cause);
  if (p.report) return 'report ready';
  if (p.hypotheses && p.hypotheses.length) {
    const top = [...p.hypotheses].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    return humanText(['top hypothesis: ' + label(top.cause || top.name), ...(p.notes || []).slice(0, 1)].join('; '));
  }
  if (p.notes?.length) return humanText(p.notes.join('; '));
  if (p.data_quality) return 'data quality ' + percent(p.data_quality.score);
  if (p.raw !== undefined) return 'raw telemetry submitted';
  return 'No summary available.';
}
function addMessage(msg) {
  state.messages.push(msg);
  renderHero(); renderPipeline(); renderRightRail(); renderBus();
  renderAgentMessages(); renderSamples();
}

function drawTopology() {
  const t = state.topology;
  if (cy) { cy.destroy(); cy = null; }
  if (!t) { $('topo').textContent = 'No topology available.'; return; }
  const bad = new Set(t.affected || []);
  const ids = new Set(t.nodes.map((n) => n.id));
  const links = t.links.filter((l) => ids.has(l.src) && ids.has(l.dst));
  // Infer upstream roots only from directed paths supplied by the API.
  const upstream = new Set(bad);
  let changed = true;
  while (changed) {
    changed = false;
    links.forEach((l) => { if (upstream.has(l.dst) && !upstream.has(l.src)) { upstream.add(l.src); changed = true; } });
  }
  const roots = new Set(t.nodes.filter((n) => upstream.has(n.id) &&
    !links.some((l) => l.dst === n.id)).map((n) => n.id));
  const cs = getComputedStyle(document.documentElement);
  const col = (n) => cs.getPropertyValue(n).trim();
  if (!t.nodes.length) { $('topo').textContent = 'No topology supplied.'; cy = null; return; }
  if (typeof cytoscape !== 'function') { $('topo').textContent = 'Topology renderer unavailable. Device metrics remain available in Device Mesh.'; return; }
  if (state.view !== 'topology') { $('topo').textContent = ''; return; }
  $('topo').textContent = '';
  cy = cytoscape({
    container: $('topo'),
    elements: [
      ...t.nodes.map((n) => ({ data: { id: n.id, label: n.id, bad: bad.has(n.id) ? 1 : 0, root: roots.has(n.id) ? 1 : 0 } })),
      ...links.map((l, i) => ({ data: { id: 'edge-' + i, source: l.src, target: l.dst } })),
    ],
    style: [
      { selector: 'node', style: { label: 'data(label)', 'font-size': 9, color: col('--fg'), 'text-valign': 'bottom',
        'background-color': col('--ok') } },
      { selector: 'node[root = 1]', style: { 'background-color': col('--amber'), width: 30, height: 30 } },
      { selector: 'node[bad = 1]', style: { 'background-color': col('--bad'), width: 28, height: 28 } },
      { selector: 'edge', style: { width: 2, 'line-color': col('--muted') } },
    ],
    layout: { name: 'breadthfirst', directed: true, padding: 10 },
  });
  cy.on('tap', 'node', (event) => renderTopologyDevice(event.target.id()));
}

// GROUP 5: all secondary views project the same run state as the console.
function renderTable(target, columns, rows, onSelect) {
  const host = $(target); host.replaceChildren();
  if (!rows.length) { empty(host, 'No data available.'); return; }
  const scroll = el('div', undefined, 'table-scroll view-table-scroll');
  scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', target.replaceAll('-', ' '));
  const table = el('table', undefined, 'view-table'), head = el('thead'), tr = el('tr'), body = el('tbody');
  columns.forEach(([label]) => { const th = el('th', label); th.scope = 'col'; tr.append(th); });
  head.append(tr);
  rows.forEach((item) => {
    const row = el('tr'); row.dataset.searchable = '';
    columns.forEach(([, get], index) => {
      const td = el('td');
      if (onSelect && index === 0) {
        const button = el('button', display(get(item)), 'table-link');
        button.disabled = state.running;
        button.addEventListener('click', () => onSelect(item)); td.append(button);
      } else td.textContent = display(get(item));
      row.append(td);
    });
    if (onSelect) row.addEventListener('click', (event) => {
      if (!state.running && !event.target.closest('button')) onSelect(item);
    });
    body.append(row);
  });
  table.append(head, body); scroll.append(table); host.append(scroll); renderSearch();
}
function renderIncidents() {
  renderTable('incidents-table', [
    ['Time (UTC)', (r) => r.created_at], ['Incident', (r) => r.incident_id || r.run_id],
    ['Cause', (r) => label(r.cause)], ['Confidence', (r) => percent(r.confidence)],
    ['Status', (r) => label(r.status)], ['Acknowledged', (r) => state.acknowledged.has(incidentKey(r)) ? 'Yes' : 'No'],
  ], state.runs || [], selectRun);
}
function selectRun(row) {
  if (state.running) return;
  resetRun(); state.runId = row.run_id; state.running = true;
  setView('console'); renderShell(); subscribeRun();
}
function renderAgentMessages() {
  const host = $('agent-messages'); host.replaceChildren();
  PIPELINE_AGENTS.forEach(([name]) => {
    const section = el('section', undefined, 'agent-message-group'); section.append(el('h3', agentName(name)));
    const list = el('ol', undefined, 'agent-message-list mono');
    runMessages().filter((m) => m.sender === name || m.recipient === name).forEach((m) => {
      const row = el('li', clockLabel(m.timestamp, true) + ' [' + agentName(m.sender) + ' → ' + agentName(m.recipient) + '] ' + summarize(m));
      row.dataset.searchable = ''; list.append(row);
    });
    if (!list.children.length) empty(section, 'No messages for this agent.');
    section.append(list); host.append(section);
  });
  renderSearch();
}
function renderSamples() {
  // Historical runs replay their SSE log, including the normalizer finding.
  const rows = findingPayload('normalizer').telemetry?.samples || NO_SAMPLES;
  if (rows !== renderedSamples) state.samplePage = 0;
  if (rows === renderedSamples && state.samplePage === renderedSamplePage) return;
  renderedSamples = rows;
  const pages = Math.max(1, Math.ceil(rows.length / 100));
  state.samplePage = Math.max(0, Math.min(state.samplePage, pages - 1));
  renderedSamplePage = state.samplePage;
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  renderTable('samples-table', keys.map((key) => [label(key), (r) => r[key]]),
    rows.slice(state.samplePage * 100, (state.samplePage + 1) * 100));
  $('samples-page').textContent = `Page ${state.samplePage + 1} of ${pages} / ${counted(rows.length, 'sample')}`;
  $('samples-prev').disabled = state.samplePage === 0;
  $('samples-next').disabled = state.samplePage >= pages - 1;
}
function renderDevices() {
  renderTable('devices-table', [
    ['Device', (d) => d.device_id], ['Type', (d) => label(d.type)], ['Role', (d) => roleLabel(d)],
    ['Location', (d) => d.location], ['Status', (d) => d.status],
    ['Worst metric', (d) => label(d.worst_metric)], ['Value', (d) => metricText(d.worst_metric, d.worst_value, d.unit)],
    ['Severity', (d) => d.severity],
  ], state.details?.devices || []);
}
function renderTopologyDevice(id) {
  const host = $('topology-device'); host.replaceChildren(); host.append(el('h3', id));
  const device = state.details?.devices?.find((d) => d.device_id === id);
  if (device) host.append(el('p', Object.entries(device).filter(([key, value]) => key !== 'device_id' && !(key === 'unit' && value === 'count'))
    .map(([key, value]) => label(key) + ': ' + (key === 'role' ? roleLabel(device) :
      ['type', 'worst_metric'].includes(key) ? label(value) : display(value))).join(' · ')));
  const evidence = (state.report?.supporting_evidence || []).filter((e) => e.device_id === id);
  evidence.forEach((e) => host.append(el('p', metricText(e.metric, e.value, e.unit) + ' — ' + humanText(e.interpretation || '—'), 'mono')));
  const samples = (findingPayload('normalizer').telemetry?.samples || []).filter((s) => s.device_id === id);
  const latest = samples.slice().sort((a, b) => (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0)).at(-1);
  if (latest) host.append(el('p', Object.entries(latest).filter(([key]) => key !== 'device_id')
    .map(([key, value]) => label(key) + ': ' + display(value)).join(' · '), 'mono'));
  if (!device && !latest && !evidence.length) empty(host, 'No device metrics available.');
}
function renderPlaybooks() {
  const host = $('playbooks-list'); host.replaceChildren();
  if (!state.playbooks?.length) { empty(host, state.playbooksError || 'No playbooks loaded.'); return; }
  state.playbooks.forEach((p) => {
    const card = el('article', undefined, 'playbook-entry'); card.dataset.searchable = '';
    card.append(el('h3', label(p.cause)));
    const list = el('ol'); (p.actions || []).forEach((a) => list.append(el('li', a)));
    card.append(list, el('p', p.remediation, 'muted')); host.append(card);
  });
  renderSearch();
}
function renderInfo() {
  ['feeds-info', 'settings-info'].forEach((id) => {
    const host = $(id); host.replaceChildren();
    const list = el('dl', undefined, 'info-list');
    const fields = [['API base', location.origin + '/api'], ['Version', state.health?.version],
      ['Uptime (seconds)', state.health?.uptime_s], ['LLM enabled', state.health?.llm_enabled],
      ...Object.entries(state.health?.limits || {})];
    fields.forEach(([key, value]) => list.append(el('dt', label(key)), el('dd', display(value), 'mono')));
    if (!state.health) host.append(el('p', 'Health information unavailable.', 'muted'));
    host.append(list);
  });
}
function renderStates() {
  $('backend-banner').hidden = !state.backendPending && !state.backendFailed;
  $('backend-retry').disabled = state.backendPending > 0;
  $('scenario').required = !$('file').files.length;
  $('run').disabled = state.running || (!state.scenariosReady && !$('file').files.length);
  $('status').hidden = !$('status').textContent && !state.error;
  $('main').setAttribute('aria-busy', String(state.running));
  if (state.error) { $('status').textContent = state.error; $('status').hidden = false; }
  $('status').classList.toggle('error-callout', !!state.error);
  $('run-error').hidden = !state.error;
  $('run-error').textContent = state.error;
}

// GROUP 3: report renderers consume only state.report and state.details.
const REMEDIABLE_CAUSES = new Set(['congestion', 'weak_wifi', 'interface_fault', 'device_overload',
  'dns_failure', 'dhcp_failure', 'hardware_failure', 'latency_degradation']);
const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
const percent = (value) => Number.isFinite(value) ? Math.round(value * 100) + '%' : '—';
function metricText(metric, value, unit) {
  return label(metric) + ' = ' + display(value) + unitSuffix(unit);
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
function sampleWindow() {
  if (state.details?.window) return state.details.window;
  const stamps = (findingPayload('normalizer').telemetry?.samples || [])
    .map((sample) => Date.parse(sample.timestamp)).filter(Number.isFinite);
  if (!stamps.length) return { first: null, last: null, seconds: 0 };
  const first = Math.min(...stamps), last = Math.max(...stamps);
  return { first: new Date(first).toISOString(), last: new Date(last).toISOString(), seconds: Math.floor((last - first) / 1000) };
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
  $('rail-confidence').textContent = inputRejected() ? '—' : percent(state.report?.confidence ?? findingPayload('correlator').diagnosis?.confidence);
  const consensus = votingConsensus();
  $('rail-accord').textContent = consensus ? consensus.k + '/' + counted(consensus.n, 'Agent') + ' in Accord' : '—';
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
    heading.append(el('span', '[' + agentName(msg.sender) + ' → ' + agentName(msg.recipient) + ']'), time);
    row.append(heading, el('p', '“' + (summarize(msg) || '—') + '”')); list.append(row);
  });
  const changed = messages.length !== renderedBusMessages.length || shared !== messages.length;
  renderedBusMessages = messages;
  $('bus-live').classList.toggle('live', state.running);
  $('bus-live').setAttribute('aria-label', state.running ? 'Bus live' : 'Bus idle');
  renderSearch();
  if (changed) list.scrollTop = list.scrollHeight;
}
function consoleMessage(sender, recipient, text, type = 'RESPONSE', rawText = false) {
  state.consoleMessages.push({ sender, recipient, type, timestamp: new Date().toISOString(), payload: { message: text, rawText } });
  renderBus();
}
async function executeCommand(command) {
  consoleMessage('You', 'Orchestrator', command, 'QUERY');
  const [verb, ...args] = command.split(/\s+/), argument = args.join(' ');
  const reply = (text, type, rawText = false) => consoleMessage('Orchestrator', 'You', text, type, rawText);
  try {
    switch (verb.toLowerCase()) {
      case 'help':
        reply('Local commands: help; run <scenario>; fail <agent> (next run; fail none clears); evidence; alts; remediate (simulated); export json.'); break;
      case 'run': {
        if (state.running) { reply('A diagnostic run is already running.'); break; }
        if (!state.scenariosReady) { reply('Scenarios are not available yet.'); break; }
        const option = [...$('scenario').options].find((o) => o.value === argument);
        if (!option) { reply('Use run <scenario>. Available: ' + [...$('scenario').options].map((o) => o.value).join(', '), 'RESPONSE', true); break; }
        $('scenario').value = option.value; $('file').value = '';
        $('clear-upload').hidden = true;
        await run(); reply($('status').textContent, state.runId ? 'RESPONSE' : 'ERROR'); break;
      }
      case 'fail': {
        const inputs = [...$('fail-options').querySelectorAll('input')];
        if (!argument || (argument !== 'none' && !inputs.some((input) => input.value === argument))) {
          reply('Use fail <agent>. Available: ' + inputs.map((input) => input.value).join(', ') + ', none'); break;
        }
        inputs.forEach((input) => { input.checked = input.value === argument; });
        state.failAgent = argument === 'none' ? null : argument;
        reply(state.failAgent ? 'next run will inject failure into ' + state.failAgent : 'Fault injection cleared for next run.'); break;
      }
      case 'evidence': {
        const evidence = state.report?.supporting_evidence;
        reply(evidence?.length ? evidence.map((e) => display(e.device_id) + ': ' + metricText(e.metric, e.value, e.unit) + ' — ' + display(e.interpretation)).join('\n') : 'No supporting evidence available.'); break;
      }
      case 'alts': {
        const alternatives = state.details?.alternatives;
        reply(alternatives?.length ? alternatives.map((a) => label(a.cause) + ': ' + percent(a.confidence) + ' — ' + humanText(a.rationale || '—')).join('\n') :
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
  $('correlation-hash').textContent = 'RUN ID: ' + (r?.run_id ? '#' + r.run_id : '—');
  const normalizer = findingPayload('normalizer');
  const formats = normalizer.formats || d?.formats || [], mappings = normalizer.mappings || d?.mappings || [];
  const availableFormats = [...new Set([...formats, ...mappings.map((m) => m.source_format)].filter(Boolean))];
  const issueCount = normalizer.data_quality?.issues?.length ?? d?.validation_issues ?? 0;
  const validation = issueCount ? counted(issueCount, 'validation issue') : 'no conflicts';
  const converted = mappings.filter((m) => m.note || m.raw_value !== m.value).length;
  $('format-normalization').textContent = 'Format Normalization: ' + (availableFormats.length ?
    availableFormats.length === 1 && !converted ? availableFormats[0] + ' · ' + validation :
      availableFormats.length + (availableFormats.length === 1 ? ' format' : ' formats') + ' reconciled (' + availableFormats.join(', ') +
      ') · ' + converted + (converted === 1 ? ' field' : ' fields') + ' converted · ' + validation : issueCount ? validation : '—');
  renderRootCause(); renderAffectedDevices(); renderEvidence(); renderTelemetry(); renderHypotheses(); renderActions();
  renderSearch();
}
function renderRootCause() {
  const r = state.report, w = sampleWindow(), box = $('summary');
  box.replaceChildren();
  const exceptional = r && ['insufficient_data', 'insufficient_evidence', 'error', 'unknown'].includes(r.status);
  box.className = 'root-cause' + (exceptional ? r.status === 'error' ? ' error' : ' insufficient' : '');
  const calloutLabel = el('p', r?.status === 'error' ? 'INPUT REJECTED' : exceptional ? 'INSUFFICIENT EVIDENCE' : 'SYNTHESIZED PROBABLE ROOT CAUSE', 'mono callout-label');
  if (!exceptional) calloutLabel.append(el('strong', r?.cause ? ' / ' + label(r.cause) : ''));
  const cause = r?.suspected_root_cause;
  box.append(calloutLabel, el('p', cause ? '“' + humanText(cause) + '”' : '—', 'root-cause-text'));
  if (!exceptional) box.append(el('p', r?.affected_devices?.length ?
    (r.affected_devices.length === 1 ? 'Affected device: ' : 'Affected devices: ') + r.affected_devices.join(', ') : '—', 'affected-sentence'));
  const meta = el('div', undefined, 'callout-meta');
  if (Number.isFinite(Date.parse(w.first)) && Number.isFinite(Date.parse(w.last)) && !inputRejected()) {
    const windowText = Date.parse(w.first) === Date.parse(w.last) ? 'single sample @ ' + clockLabel(w.first) :
      clockLabel(w.first) + ' – ' + clockLabel(w.last);
    meta.append(el('span', 'Trigger Window: ' + windowText + ' UTC'));
  }
  if (!inputRejected()) meta.append(el('span', 'Multi-Agent Consensus: ' + percent(r?.confidence) + ' ' + label(r?.confidence_category) + ' Confidence'));
  if (meta.children.length) box.append(meta);
  const limitations = r?.limitations || [];
  $('report-limitations').hidden = !limitations.length;
  $('limitations-label').textContent = 'Limitations (' + limitations.length + ')';
  $('limitations-list').replaceChildren(...limitations.map((item) => el('li', humanText(item))));
}
function renderAffectedDevices() {
  const grid = $('affected-devices'), ids = state.report?.affected_devices || [];
  grid.replaceChildren();
  ids.slice(0, 3).forEach((id) => {
    const d = state.details?.devices?.find((device) => device.device_id === id);
    const card = el('article', undefined, 'device-card');
    card.append(el('span', roleLabel(d), 'mono stat-label'), el('strong', id),
      el('p', [d?.location, d?.type ? label(d.type) : null].filter(Boolean).join(' · ') || '—', 'device-description'));
    card.append(el('span', d?.worst_metric ? label(d.worst_metric) + ' ' + display(d.worst_value) + unitSuffix(d.unit) +
      (d.severity ? ' · ' + d.severity : '') : '—',
      'pill ' + (d?.severity === 'critical' ? 'alert' : d?.severity === 'warning' ? 'flagged' : '')));
    grid.append(card);
  });
  if (!ids.length) empty(grid, state.report ? 'No affected devices identified.' : '—');
  if (ids.length > 3) grid.append(el('p', '+' + (ids.length - 3) + ' more', 'muted more-devices'));
}
function renderEvidence() {
  const grid = $('evidence'), rows = state.report?.supporting_evidence || [];
  grid.replaceChildren();
  const window = sampleWindow(), seconds = window.seconds;
  $('sample-window').textContent = Number.isFinite(seconds) && seconds > 0 ?
    'SAMPLED OVER ' + counted(seconds, 'SECOND') : window.first ? 'SINGLE SAMPLE' : 'NO SAMPLES';
  rows.slice(0, 5).forEach((e) => {
    const tile = el('article', undefined, 'evidence-tile');
    tile.dataset.searchable = '';
    const value = el('strong', display(e.value), 'metric-value ' + evidenceTone(e));
    if (unitSuffix(e.unit)) value.append(el('span', unitSuffix(e.unit), 'metric-unit'));
    tile.append(el('span', label(e.metric), 'mono stat-label'),
      value,
      el('span', e.baseline != null ? 'Baseline: ' + e.baseline + unitSuffix(e.unit) :
        e.threshold != null ? 'Threshold: ' + e.threshold + unitSuffix(e.unit) : '—', 'mono evidence-detail'),
      el('span', display(e.device_id), 'mono evidence-detail'));
    tile.title = humanText(e.interpretation); grid.append(tile);
  });
  if (!rows.length) empty(grid, state.report ? 'No supporting evidence available.' : '—');
}
function renderTelemetry() {
  const body = $('telemetry-rows'), affected = new Set(state.report?.affected_devices || []);
  const evidence = state.report?.supporting_evidence || [];
  const priority = (m) => evidence.some((e) => e.device_id === m.device_id && e.metric === m.metric) ? 0 :
    m.note || m.raw_value !== m.value ? 1 : 2;
  const rows = (state.details?.mappings || []).filter((m) => affected.has(m.device_id))
    .slice().sort((a, b) => priority(a) - priority(b));
  body.replaceChildren();
  (state.telemetryExpanded ? rows : rows.slice(0, 6)).forEach((m) => {
    const row = el('tr'); row.dataset.searchable = '';
    row.append(el('td', display(m.source_format), 'telemetry-feed'), el('td', display(m.device_id)),
      el('td', display(m.raw_path) + (m.raw_value === m.value && !m.note ? '' : ' = ' + display(m.raw_value)), 'raw-fragment'),
      el('td', metricText(m.metric, m.value, m.unit), evidenceTone(m)));
    row.title = m.note || ''; body.append(row);
  });
  $('telemetry-empty').textContent = rows.length ? '' : state.report ? 'No normalization mappings for affected devices.' : '—';
  $('telemetry-empty').hidden = !!rows.length;
  $('telemetry-more').hidden = rows.length <= 6;
  $('telemetry-more').textContent = state.telemetryExpanded ? 'Show less' : '+' + (rows.length - 6) + ' more';
  $('telemetry-more').setAttribute('aria-expanded', String(state.telemetryExpanded));
}
$('telemetry-more').onclick = () => {
  state.telemetryExpanded = !state.telemetryExpanded; renderTelemetry(); renderSearch();
};
// Printed report shows every telemetry row and the limitations; restore the screen state afterwards.
let printRestore = null;
window.addEventListener('beforeprint', () => {
  printRestore = { rows: state.telemetryExpanded, limits: $('report-limitations').open };
  state.telemetryExpanded = true; $('report-limitations').open = true; renderTelemetry();
});
window.addEventListener('afterprint', () => {
  if (!printRestore) return;
  state.telemetryExpanded = printRestore.rows; $('report-limitations').open = printRestore.limits;
  printRestore = null; renderTelemetry(); renderSearch();
});
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
  const rejected = inputRejected();
  $('hypotheses-section').hidden = rejected;
  $('hypotheses-empty').hidden = !rejected;
  if (rejected) return;
  const devices = r?.affected_devices?.join(', ');
  primary.append(el('p', 'Primary Hypothesis: ' + label(r?.cause) + (devices ? ' on ' + devices : '') +
    ' — ' + percent(r?.confidence) + ' (' + label(r?.confidence_category) + ' Confidence)', 'primary-title mono'),
    confidenceBar(r?.confidence), el('p', humanText(d?.primary_rationale) || '—', 'hypothesis-rationale'));
  // /details exposes each analysis agent's top hypothesis and its actual score.
  const rejectedHypotheses = (d?.agents || [])
    .filter((a) => ['performance', 'infrastructure', 'connectivity'].includes(a.name) &&
      a.top_hypothesis && a.top_hypothesis !== r?.cause && Number.isFinite(a.score))
    .sort((a, b) => b.score - a.score)
    .filter((a, i, all) => all.findIndex((other) => other.top_hypothesis === a.top_hypothesis) === i)
    .slice(0, 2).map((a) => ({ cause: a.top_hypothesis, confidence: a.score, rationale: a.summary, rejected: true }));
  const alternatives = r?.alternative_causes?.length ?
    d?.alternatives?.length ? d.alternatives : r.alternative_causes.map((cause) => ({ cause })) :
    d?.alternatives?.length ? [...d.alternatives].sort((a, b) => b.confidence - a.confidence)
      .slice(0, 2).map((a) => ({ ...a, rejected: true })) : rejectedHypotheses;
  alternatives.forEach((a) => {
    const known = Number.isFinite(a.confidence), ruledOut = a.rejected || known && a.confidence < 0.2;
    const card = el('article', undefined, 'alternative-card' + (known ? ruledOut ? ' ruled-out' : ' partial' : ''));
    const heading = el('div', undefined, 'alternative-heading');
    // Agent-level hypotheses the correlator did not pick are scores from one agent, not ruled-out causes.
    const agentView = alternatives === rejectedHypotheses;
    heading.append(el('strong', label(a.cause)),
      el('span', percent(a.confidence) + (agentView ? ' Agent Score' : ' Heuristic Score'), 'mono'));
    const rationale = el('p', undefined, 'hypothesis-rationale');
    rationale.append(el('strong', a.overruled_by ? 'Overruled by: ' : agentView ? 'Set Aside by Correlator: ' :
      known ? ruledOut ? 'Ruled Out: ' : 'Partial Contributor: ' : '— '), el('span', humanText(a.overruled_by || a.rationale) || '—'));
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
    'Simulated remediation: ' + label(result.before) + ' → ' + label(result.after) + ' — verified ' + (result.verified ? '✓' : '✗') : '';
  $('fixresult').className = result ? 'remediation-result ' + (result.verified ? 'pass' : 'fail') : '';
}
window.openRemediateModal = () => {
  if (!canRemediate()) { $('status').textContent = 'Run a supported diagnosis to approve simulated remediation.'; renderStates(); return; }
  $('remediate-target').textContent = 'Run: ' + state.runId + ' · Cause: ' + label(state.report.cause);
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

function resetRun() {
  clearTimeout(recoveryTimer); recoveryTimer = null;
  if (source) { source.close(); source = null; }
  state.error = ''; state.remediation = null; $('remediate-modal').close();
  state.runId = null; state.report = null; state.details = null; state.topology = null;
  state.messages = []; state.consoleMessages = [];
  state.telemetryExpanded = false; $('report-limitations').open = false;
  if (cy) { cy.destroy(); cy = null; }
  $('topo').textContent = '';
  $('topology-device').replaceChildren(el('p', 'Select a node to inspect its metrics.', 'muted'));
  $('status').textContent = '';
}
async function run(useUpload = true) {
  if (state.running) return;
  const f = useUpload ? $('file').files[0] : null;
  if (f && f.size > 5_000_000) {
    state.error = 'Upload exceeds the 5 MB limit.'; renderStates(); return;
  }
  resetRun(); state.running = true;
  closeExportMenu();
  $('run').disabled = true; $('status').textContent = 'Starting…';
  setView('console'); renderShell();
  try {
    const body = { fail_agent: state.failAgent };
    if (f) body.upload_id = (await json('/api/upload', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: await f.text() })).upload_id;
    else body.scenario = $('scenario').value || 'congestion';
    state.runId = (await json('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) })).run_id;
    state.failAgent = null;
    $('fail-options').querySelectorAll('input').forEach((input) => { input.checked = false; });
    $('status').textContent = 'Run ' + state.runId + (body.fail_agent ? ' · failure injection: ' + body.fail_agent : '');
    $('run-modal').close();
    renderShell(); subscribeRun();
  } catch (err) {
    state.error = 'Error: ' + err.message;
    state.running = false; renderShell();
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
$('trigger-run').onclick = () => void run(false);
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
    catch (err) { state.error = 'Export error: ' + err.message; renderStates(); }
  };
});
document.addEventListener('click', (event) => { if (!event.target.closest('.export-control')) closeExportMenu(); });
// Keep relative sweep time current without refetching the finished run.
setInterval(() => { if (state.details?.finished_at) renderHero(); }, 1000);
$('run-form').addEventListener('submit', (event) => { event.preventDefault(); void run(); });
$('samples-prev').onclick = () => { state.samplePage--; renderSamples(); };
$('samples-next').onclick = () => { state.samplePage++; renderSamples(); };
// /api/run accepts one fail_agent; checkboxes are mutually exclusive.
['normalizer', 'performance', 'infrastructure', 'connectivity', 'correlator', 'reporter'].forEach((name) => {
  const label = el('label'), input = el('input'); input.type = 'checkbox'; input.value = name;
  input.addEventListener('change', () => {
    if (input.checked) $('fail-options').querySelectorAll('input').forEach((other) => {
      if (other !== input) other.checked = false;
    });
    state.failAgent = $('fail-options').querySelector('input:checked')?.value || null;
  });
  label.append(input, el('span', name)); $('fail-options').append(label);
});
window.openRunModal = () => {
  if (!$('run-modal').open) $('run-modal').showModal();
  $('scenario').focus();
};
$('run-cancel').onclick = () => $('run-modal').close();
$('file').addEventListener('change', () => {
  $('clear-upload').hidden = !$('file').files.length;
  $('status').textContent = '';
  state.error = $('file').files[0]?.size > 5_000_000 ? 'Upload exceeds the 5 MB limit.' : '';
  renderStates();
});
$('clear-upload').onclick = () => {
  $('file').value = ''; $('clear-upload').hidden = true;
  state.error = ''; $('status').textContent = ''; renderStates();
};
$('scenario').addEventListener('change', () => {
  $('file').value = ''; $('clear-upload').hidden = true;
  state.error = ''; $('status').textContent = ''; renderStates();
});
$('backend-retry').onclick = async () => {
  state.backendFailed = false; state.error = ''; renderStates();
  try { await init(); }
  catch (err) { state.backendFailed = true; state.error = err.message; renderStates(); }
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
  if (event.key === 'Escape' && !$('export-menu').hidden) { closeExportMenu(); $('export-toggle').focus(); }
  if (event.key === 'Escape') { $('sidebar').classList.remove('open'); $('sidebar-toggle').setAttribute('aria-expanded', 'false'); }
});
$('spawn-run').onclick = () => {
  window.openRunModal();
};
$('quarantine').onclick = () => window.openRemediateModal();
$('mobile-quarantine').onclick = () => {
  $('mobile-overflow').open = false; window.openRemediateModal();
};
document.addEventListener('click', (event) => {
  if (!event.target.closest('#mobile-overflow')) $('mobile-overflow').open = false;
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && $('mobile-overflow').open) {
    $('mobile-overflow').open = false; $('mobile-overflow').querySelector('summary').focus();
  }
});
$('acknowledge-all').onclick = () => {
  // Acknowledge all currently listed incidents, including the selected run.
  const runs = [...(state.runs || [])];
  if (state.report) runs.push({ ...state.report, run_id: state.runId });
  runs.forEach((r) => { const key = incidentKey(r); if (key) state.acknowledged.add(key); });
  try { localStorage.setItem(ACK_KEY, JSON.stringify([...state.acknowledged])); }
  catch { $('status').textContent = 'Acknowledged for this session; browser storage is unavailable.'; }
  renderSidebar(); renderIncidents(); renderStates();
};
$('search').addEventListener('input', () => { state.search = $('search').value.trim().toLowerCase(); renderSearch(); });
renderViews(); renderShell();
init().catch((e) => { state.error = 'Error: ' + e.message; renderStates(); });
