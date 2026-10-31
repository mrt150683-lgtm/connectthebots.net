"""Disposable QA fixture control over parent-only stdin, never an HTTP API.
Fresh vault key lives only in process memory. Tokens cross only the private
child stdout pipe; the browser driver never records that pipe or its contents.
"""
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
import json, secrets, sys, threading, time, tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from agentmatch_runtime.runtime import AgentRuntime, AgentRuntimeError
from agentmatch_runtime.http_server import AgentApp, handler

output = ROOT / 'artifacts/operational-fixes/viewer'
output.mkdir(parents=True, exist_ok=True)
folder = tempfile.TemporaryDirectory(prefix='isolated-', dir=output)
database = Path(folder.name) / 'qa.sqlite'
key = secrets.token_bytes(32)
now = datetime.now(timezone.utc)
runtime = AgentRuntime.create(database, key, clock=lambda: now)
admin = runtime.issue_credential('marlow', 'agent', ['workspace', 'save_brief'], max_uses=5000)
server = None
port = 0
delay = 0

class DelayedApp(AgentApp):
    def handle(self, method, target, headers, body=b''):
        if delay and target.startswith('/v1/workspace'):
            time.sleep(delay)
        return super().handle(method, target, headers, body)

def start():
    global server, port, runtime
    runtime = AgentRuntime.open(database, key, clock=lambda: now)
    app = DelayedApp(runtime, port=port, viewer_dir=ROOT / 'workbench/viewer')
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(app))
    port = server.server_port
    app.port = port
    app.hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{port}/'

def stop():
    global server
    if server:
        server.shutdown(); server.server_close(); server = None

def reply(value):
    print(json.dumps(value), flush=True)

reply({'url': start()})
try:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request['action']
            if action == 'issue':
                result = runtime.issue_credential('marlow', request.get('role', 'viewer'), ['workspace'],
                    ttl_seconds=request.get('ttl', 3600), max_uses=request.get('uses', 500))
            elif action == 'revoke':
                result = runtime.revoke_credential(request['id'])
            elif action == 'advance':
                now += timedelta(seconds=request['seconds']); result = {'advanced': True}
            elif action == 'state':
                result = {'capability': runtime.capabilities(request['token']),
                          'receipts': runtime.receipts(request['token'], after=request.get('after', 0),
                              limit=request.get('limit', 50), order=request.get('order', 'desc'), before=request.get('before'))}
            elif action == 'spend':
                runtime.execute(request['token'], 'workspace', {})
                result = {'spent': True}
            elif action == 'seed_receipts':
                for _ in range(request['count']):
                    runtime.execute(admin['token'], 'workspace', {})
                observer = runtime.issue_credential('marlow', 'agent', ['workspace'])
                try:
                    runtime.execute(observer['token'], 'save_brief', {}, expected_version=runtime.capabilities(observer['token'])['version'], idempotency_key='qa-denied')
                except AgentRuntimeError:
                    pass
                result = {'count': request['count'], 'latest': 'SCOPE_REQUIRED'}
            elif action == 'title':
                ws = runtime.execute(admin['token'], 'workspace', {})
                brief = dict(ws['result']['brief']); version = brief.pop('version')
                brief.pop('brief_id'); brief.pop('updated_at'); brief['title'] = request['value']
                if 'outcome' in request:
                    brief['desired_outcome'] = request['outcome']
                changed = runtime.execute(admin['token'], 'save_brief', {'expected_version': version, 'brief': brief},
                    expected_version=ws['version'], idempotency_key=secrets.token_hex(8))
                result = {'version': changed['version']}
            elif action == 'stop':
                stop(); result = {'stopped': True}
            elif action == 'start':
                result = {'url': start()}
            elif action == 'delay':
                delay = request['seconds']; result = {'delay': delay}
            elif action == 'exit':
                reply({'exiting': True}); break
            else:
                result = {'error': 'UNKNOWN_ACTION'}
            reply(result)
        except Exception as exc:
            reply({'error': type(exc).__name__})
finally:
    stop()
    folder.cleanup()
