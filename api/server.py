"""FastAPI server: run diagnoses, stream bus messages (SSE), serve the dashboard."""
import asyncio
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netcoop.cli import _cause, _report, build_orchestrator
from netcoop.playbook import simulate_remediation
from netcoop.state import RunState

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'data' / 'scenarios'
STATIC = Path(__file__).resolve().parent / 'static'
_ID = re.compile(r'^[A-Za-z0-9_.-]+$')


class RunRequest(BaseModel):
    scenario: str | None = None
    upload_id: str | None = None
    fail_agent: str | None = None


def create_app(runs_dir: str | Path | None = None) -> FastAPI:
    runs = Path(runs_dir or os.getenv('NETCOOP_RUNS_DIR', 'runs'))
    app = FastAPI(title='NetCoop AI')
    tasks: dict[str, asyncio.Task] = {}

    def safe(value: str) -> str:
        if not _ID.match(value) or value.startswith('.'):
            raise HTTPException(400, 'invalid identifier')
        return value

    def run_dir(run_id: str) -> Path:
        path = runs / safe(run_id)
        if not path.is_dir():
            raise HTTPException(404, 'unknown run')
        return path

    async def finished(run_id: str) -> None:
        task = tasks.get(run_id)
        if task is not None:
            await task

    async def execute(raw: str, run_id: str, fail: set[str]) -> None:
        try:
            await build_orchestrator(runs, fail, no_llm=True).run(raw, run_id=run_id)
        except Exception as exc:  # never leave a stream hanging
            (runs / run_id).mkdir(parents=True, exist_ok=True)
            (runs / run_id / 'fatal.txt').write_text(str(exc), encoding='utf-8')

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
        body = await request.body()
        if not body.strip():
            raise HTTPException(400, 'empty upload')
        if len(body) > 5_000_000:
            raise HTTPException(413, 'upload too large')
        upload_id = uuid4().hex
        folder = runs / 'uploads'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{upload_id}.json').write_bytes(body)
        return {'upload_id': upload_id}

    @app.post('/api/run')
    async def start_run(req: RunRequest) -> dict:
        if req.scenario:
            path = SCENARIOS / f'{safe(req.scenario)}.json'
        elif req.upload_id:
            path = runs / 'uploads' / f'{safe(req.upload_id)}.json'
        else:
            raise HTTPException(400, 'provide scenario or upload_id')
        if not path.is_file():
            raise HTTPException(404, 'input not found')
        raw = path.read_text(encoding='utf-8', errors='replace')
        run_id = uuid4().hex[:12]
        fail = {safe(req.fail_agent)} if req.fail_agent else set()
        tasks[run_id] = asyncio.create_task(execute(raw, run_id, fail))
        return {'run_id': run_id}

    @app.get('/api/runs/{run_id}/stream')
    async def stream(run_id: str):
        safe(run_id)
        log = runs / run_id / 'messages.jsonl'

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
                            yield f'data: {line}\n\n'
                if done:
                    yield 'event: done\ndata: {}\n\n'
                    return
                await asyncio.sleep(0.15)

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache'})

    @app.get('/api/runs/{run_id}/report')
    async def report(run_id: str):
        directory = run_dir(run_id)
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
        raise HTTPException(500, 'run produced no report')

    @app.get('/api/runs/{run_id}/topology')
    async def topology(run_id: str) -> dict:
        directory = run_dir(run_id)
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
    async def remediate(run_id: str) -> dict:
        directory = run_dir(run_id)
        await finished(run_id)
        try:
            state = RunState.model_validate_json((directory / 'state.json').read_text(encoding='utf-8'))
            before = _cause(state, _report(state))
            if state.telemetry is None:
                raise ValueError('run has no normalized telemetry')
            patched = simulate_remediation(before, state.telemetry)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, f'cannot remediate: {exc}')
        new_id = uuid4().hex[:12]
        result = await build_orchestrator(runs, no_llm=True).run(patched, run_id=new_id)
        after = _cause(result, _report(result))
        return {'before': before, 'after': after, 'verified': after == 'normal', 'run_id': new_id}

    app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
    return app


app = create_app()
