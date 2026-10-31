"""FastAPI server: run diagnoses, stream bus messages (SSE), serve the dashboard."""
import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netcoop.cli import _cause, _report, build_orchestrator
from netcoop import __version__
from netcoop.agents.normalizer import MAX_DEVICES, MAX_SAMPLES, MAX_NODES, MAX_LINKS, MAX_SERVICES
from netcoop.details import build_details
from netcoop.playbook import PLAYBOOK, simulate_remediation
from netcoop.state import RunState

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'data' / 'scenarios'
STATIC = Path(__file__).resolve().parent / 'static'
log = logging.getLogger(__name__)
_ID = re.compile(r'^[A-Za-z0-9_.-]+$')
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; connect-src 'self'"),
}
MAX_UPLOAD_BYTES = 5_000_000
MAX_PENDING_UPLOADS = 5
MAX_UPLOAD_FILES = 200
MAX_UPLOAD_TOTAL_BYTES = 100_000_000
MAX_CONCURRENT_RUNS = 2
RATE_LIMIT = 10  # runs per client IP per RATE_WINDOW_S
RATE_WINDOW_S = 60
RUN_LIST_LIMIT = 20


class RunRequest(BaseModel):
    scenario: str | None = None
    upload_id: str | None = None
    fail_agent: str | None = None


def _bounds(path: Path) -> list[dict]:
    """First and last bus messages of a run (timestamps only are needed)."""
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
    out = []
    for line in (lines[:1] + lines[-1:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def create_app(runs_dir: str | Path | None = None) -> FastAPI:
    runs = Path(runs_dir or os.getenv('NETCOOP_RUNS_DIR', 'runs'))
    app = FastAPI(title='NetCoop AI')

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        session = request.cookies.get('nc_session')
        new_session = not session
        request.state.session = session or uuid4().hex
        response = await call_next(request)
        if new_session:
            # Render terminates TLS, so the app itself sees plain http.
            https = request.headers.get('x-forwarded-proto', request.url.scheme) == 'https'
            response.set_cookie('nc_session', request.state.session, httponly=True,
                                samesite='lax', path='/', secure=https)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    tasks: dict[str, asyncio.Task] = {}
    finished_at: dict[str, float] = {}
    started = time.monotonic()
    hit_log: dict[str, deque] = {}
    upload_hit_log: dict[str, deque] = {}
    active = [0]

    def safe(value: str) -> str:
        if not _ID.match(value) or value.startswith('.') or value == 'labels':
            raise HTTPException(400, 'invalid identifier')
        return value

    def owned(path: Path, session: str) -> bool:
        try:
            return path.read_text(encoding='utf-8') == session
        except OSError:
            return False

    def run_dir(run_id: str, request: Request) -> Path:
        path = runs / safe(run_id)
        if not path.is_dir() or not owned(path / 'owner.txt', request.state.session):
            raise HTTPException(404, 'unknown run')
        return path

    def create_run(run_id: str, session: str) -> None:
        directory = runs / run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'owner.txt').write_text(session, encoding='utf-8')

    async def finished(run_id: str) -> None:
        task = tasks.get(run_id)
        if task is not None:
            await task

    def run_sync(raw: str, run_id: str, fail: set[str]):
        # Own event loop in a worker thread: agents do sync CPU work that would
        # otherwise freeze the API loop (and every SSE stream with it).
        return asyncio.run(build_orchestrator(runs, fail, no_llm=True).run(raw, run_id=run_id))

    def rate_limit(request: Request, history: dict[str, deque], kind: str) -> None:
        now = time.monotonic()
        hits = history.setdefault(request.client.host if request.client else '?', deque())
        while hits and now - hits[0] > RATE_WINDOW_S:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            raise HTTPException(429, f'too many {kind}; retry in a minute', headers={'Retry-After': '60'})
        hits.append(now)

    def admit(request: Request) -> None:
        """Check concurrency before recording an admitted run's rate-limit hit."""
        if active[0] >= MAX_CONCURRENT_RUNS:
            raise HTTPException(429, 'server busy; retry shortly', headers={'Retry-After': '60'})
        rate_limit(request, hit_log, 'runs')
        active[0] += 1

    def release() -> None:
        active[0] -= 1

    def cleanup() -> None:
        """Drop run/upload dirs and finished task records older than the TTL."""
        ttl = float(os.getenv('NETCOOP_RUN_TTL_S', '3600'))
        cutoff = time.time() - ttl
        for run_id in [r for r, t in finished_at.items() if t < time.monotonic() - ttl]:
            finished_at.pop(run_id, None)
            tasks.pop(run_id, None)
        if not runs.is_dir():
            return
        for parent in (runs, runs / 'uploads'):
            if not parent.is_dir():
                continue
            for entry in parent.iterdir():
                if entry.name == 'uploads' and parent == runs:
                    continue
                task = tasks.get(entry.name)
                if task is not None and not task.done():
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
                except OSError:
                    pass

    async def execute(raw: str, run_id: str, fail: set[str]) -> None:
        try:
            await asyncio.to_thread(run_sync, raw, run_id, fail)
        except Exception as exc:  # never leave a stream hanging
            (runs / run_id).mkdir(parents=True, exist_ok=True)
            (runs / run_id / 'fatal.txt').write_text(str(exc), encoding='utf-8')
        finally:
            finished_at[run_id] = time.monotonic()
            release()

    @app.get('/api/scenarios')
    def list_scenarios() -> list[dict]:
        labels = json.loads((SCENARIOS / 'labels.json').read_text(encoding='utf-8'))
        return [{'name': name, 'expected': expected} for name, expected in labels.items()]

    @app.get('/api/scenarios/{name}')
    def get_scenario(name: str):
        path = SCENARIOS / f'{safe(name)}.json'
        if not path.is_file():
            raise HTTPException(404, 'unknown scenario')
        return FileResponse(path, media_type='application/json')

    @app.post('/api/upload')
    async def upload(request: Request) -> dict:
        cleanup()
        rate_limit(request, upload_hit_log, 'uploads')
        declared = request.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, 'upload too large')
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, 'upload too large')
            chunks.append(chunk)
        body = b''.join(chunks)
        if not body.strip():
            raise HTTPException(400, 'empty upload')
        upload_id = uuid4().hex
        folder = runs / 'uploads'
        folder.mkdir(parents=True, exist_ok=True)
        files = list(folder.glob('*.json'))
        if sum(owned(path.with_suffix('.owner'), request.state.session)
               for path in files) >= MAX_PENDING_UPLOADS:
            raise HTTPException(413, 'too many pending uploads')
        if (len(files) >= MAX_UPLOAD_FILES
                or sum(path.stat().st_size for path in files) + size > MAX_UPLOAD_TOTAL_BYTES):
            raise HTTPException(413, 'upload storage limit exceeded')
        (folder / f'{upload_id}.json').write_bytes(body)
        (folder / f'{upload_id}.owner').write_text(request.state.session, encoding='utf-8')
        return {'upload_id': upload_id}

    @app.post('/api/run')
    async def start_run(req: RunRequest, request: Request) -> dict:
        if (req.scenario is None) == (req.upload_id is None):
            raise HTTPException(422, 'provide exactly one of scenario or upload_id')
        if req.scenario is not None:
            path = SCENARIOS / f'{safe(req.scenario)}.json'
        else:
            path = runs / 'uploads' / f'{safe(req.upload_id)}.json'
            if not owned(path.with_suffix('.owner'), request.state.session):
                raise HTTPException(404, 'input not found')
        if not path.is_file():
            raise HTTPException(404, 'input not found')
        raw = path.read_text(encoding='utf-8', errors='replace')
        run_id = uuid4().hex[:12]
        fail = {safe(req.fail_agent)} if req.fail_agent else set()
        cleanup()
        admit(request)
        try:
            create_run(run_id, request.state.session)
            if req.upload_id is not None:
                path.unlink()
                path.with_suffix('.owner').unlink(missing_ok=True)
        except OSError:
            release()
            raise
        tasks[run_id] = asyncio.create_task(execute(raw, run_id, fail))
        return {'run_id': run_id}

    @app.get('/api/runs/{run_id}/stream')
    async def stream(run_id: str, request: Request):
        log = run_dir(run_id, request) / 'messages.jsonl'

        async def events():
            offset = 0
            while True:
                task = tasks.get(run_id)
                done = task is None or task.done()
                if log.is_file():
                    with log.open('rb') as handle:
                        handle.seek(offset)
                        data = handle.read()
                    cut = data.rfind(b'\n') + 1
                    offset += cut
                    for line in data[:cut].decode('utf-8', 'replace').splitlines():
                        if line.strip():
                            message = json.loads(line)
                            payload = message.get('payload', {})
                            if 'raw' in payload:
                                raw = payload['raw']
                                raw_text = raw if isinstance(raw, str) else json.dumps(raw)
                                size = len(raw_text.encode('utf-8'))
                                payload['raw'] = f'[raw input redacted: {size} bytes]'
                            yield f'data: {json.dumps(message)}\n\n'
                if done:
                    yield 'event: done\ndata: {}\n\n'
                    return
                await asyncio.sleep(0.15)

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache'})

    @app.get('/api/runs/{run_id}/report')
    async def report(run_id: str, request: Request):
        directory = run_dir(run_id, request)
        await finished(run_id)
        path = directory / 'report.json'
        state_path = directory / 'state.json'
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            try:
                state = RunState.model_validate_json(state_path.read_text(encoding='utf-8'))
                data['cause'] = _cause(state, _report(state))
            except (OSError, ValueError, KeyError):
                data['cause'] = data.get('status')
            return data
        if state_path.is_file():
            state = RunState.model_validate_json(state_path.read_text(encoding='utf-8'))
            reason = state.limitations[0] if state.limitations else 'diagnosis failed'
            data = _report({'error': reason, 'run_id': run_id,
                            'limitations': state.limitations}).model_dump(mode='json')
            data['cause'] = 'error'
            return data
        reason = 'run failed before producing a report'
        data = _report({'error': reason, 'run_id': run_id,
                        'limitations': [reason]}).model_dump(mode='json')
        data['cause'] = 'error'
        return data

    @app.get('/api/runs/{run_id}/report.md')
    async def report_markdown(run_id: str, request: Request):
        directory = run_dir(run_id, request)
        await finished(run_id)
        path = directory / 'report.md'
        if not path.is_file():
            raise HTTPException(404, 'no report for this run')
        return FileResponse(path, media_type='text/markdown; charset=utf-8')

    @app.get('/api/runs/{run_id}/details')
    async def details(run_id: str, request: Request) -> dict:
        directory = run_dir(run_id, request)
        await finished(run_id)
        state_path = directory / 'state.json'
        if not state_path.is_file():
            raise HTTPException(404, 'run has no state')
        try:
            state = RunState.model_validate_json(state_path.read_text(encoding='utf-8'))
        except ValueError:
            raise HTTPException(409, 'run state is unreadable')
        return await asyncio.to_thread(build_details, state, _bounds(directory / 'messages.jsonl'))

    @app.get('/api/runs')
    async def list_runs(request: Request) -> list[dict]:
        rows = []
        if not runs.is_dir():
            return rows
        entries = [e for e in runs.iterdir() if e.is_dir() and e.name != 'uploads'
                   and owned(e / 'owner.txt', request.state.session)]
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        for entry in entries[:RUN_LIST_LIMIT]:
            task = tasks.get(entry.name)
            created = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc).isoformat()
            report_path = entry / 'report.json'
            if report_path.is_file():
                try:
                    data = json.loads(report_path.read_text(encoding='utf-8'))
                except ValueError:
                    continue
                rows.append({'run_id': entry.name, 'incident_id': data.get('incident_id'),
                             'cause': data.get('cause') or data.get('status'),
                             'status': data.get('status'), 'confidence': data.get('confidence'),
                             'created_at': created})
            elif (task is not None or any((entry / name).is_file()
                                         for name in ('state.json', 'fatal.txt', 'messages.jsonl'))):
                running = task is not None and not task.done()
                rows.append({'run_id': entry.name, 'incident_id': None,
                             'cause': None if running else 'error',
                             'status': 'running' if running else 'error',
                             'confidence': None, 'created_at': created})
        return rows

    @app.get('/api/playbook')
    def playbook() -> list[dict]:
        return [{'cause': cause, 'actions': item['actions'], 'remediation': item['remediation']}
                for cause, item in PLAYBOOK.items()]

    @app.get('/api/health')
    def health() -> dict:
        return {'version': __version__, 'uptime_s': round(time.monotonic() - started, 1),
                'llm_enabled': False,
                'limits': {'max_upload_mb': MAX_UPLOAD_BYTES // 1_000_000, 'max_devices': MAX_DEVICES,
                           'max_samples': MAX_SAMPLES, 'max_nodes': MAX_NODES,
                           'max_links': MAX_LINKS, 'max_services': MAX_SERVICES,
                           'runs_per_min': RATE_LIMIT}}

    @app.get('/api/runs/{run_id}/topology')
    async def topology(run_id: str, request: Request) -> dict:
        directory = run_dir(run_id, request)
        await finished(run_id)
        state_path = directory / 'state.json'
        telemetry = (RunState.model_validate_json(state_path.read_text(encoding='utf-8')).telemetry
                     if state_path.is_file() else None) or {}
        affected: list[str] = []
        report_path = directory / 'report.json'
        if report_path.is_file():
            affected = json.loads(report_path.read_text(encoding='utf-8')).get('affected_devices', [])
        topo = telemetry.get('topology') or {}
        return {'nodes': topo.get('nodes', []), 'links': topo.get('links', []), 'affected': affected}

    @app.post('/api/runs/{run_id}/remediate')
    async def remediate(run_id: str, request: Request) -> dict:
        directory = run_dir(run_id, request)
        await finished(run_id)
        admit(request)
        try:
            return await _remediate(directory, request.state.session)
        finally:
            release()

    async def _remediate(directory: Path, session: str) -> dict:
        try:
            state = RunState.model_validate_json((directory / 'state.json').read_text(encoding='utf-8'))
            before = _cause(state, _report(state))
            if state.telemetry is None:
                raise ValueError('run has no normalized telemetry')
            patched = simulate_remediation(before, state.telemetry)
        except (OSError, ValueError, KeyError) as exc:
            log.info('remediate refused: %s', exc)
            raise HTTPException(409, 'cannot remediate: run has no usable diagnosis')
        new_id = uuid4().hex[:12]
        create_run(new_id, session)
        result = await asyncio.to_thread(run_sync, patched, new_id, set())
        after = _cause(result, _report(result))
        return {'before': before, 'after': after, 'verified': after == 'normal', 'run_id': new_id}

    app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
    return app


app = create_app()
