"""Phase5 disposable acceptance controller; never touches the development schema.

Uses the installed MCP's verified temporary-schema/account provisioner. Credentials
stay in memory. The proxy can discard a SUCCESSFUL response for explicit QA only.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path.home() / '.codex/mcp/deepseek'))
from workspace import Workspace
from integration import TestDatabase, child_environment, stop_process, run_process
import httpx
from sqlalchemy import create_engine, URL, text
from sqlalchemy.orm import Session
from pwdlib import PasswordHash
from app.db import models  # noqa: F401
from app.modules.users.models import User
from app.core.config import Settings

META = ROOT / 'Frontend/PHASE5_TEST_FIXTURE.json'
REPORT = ROOT / 'Frontend/PHASE5_HTTP_RESULTS.json'
API = 'http://127.0.0.1:8019/api/v1'
PASSWORD = os.environ['PHASE5_PASSWORD']
stop = threading.Event()
fault = {'create': 0, 'complete': 0, 'cancel': 0}
lock = threading.Lock()
requests = []
processes, logs = [], []

def call(headers, method, path, body=None, expected=200, key=None):
    response = httpx.request(method, API + path, headers={**headers, **({'Idempotency-Key': key} if key else {})}, json=body, timeout=20)
    assert response.status_code == expected, (method, path, response.status_code)
    return response.json() if expected != 204 else None

def login(name):
    return {'Authorization': 'Bearer ' + call({}, 'POST', '/auth/login', {'identifier': 'qa5_' + name, 'password': PASSWORD})['access_token']}

def seed():
    identities = {}
    platform = login('platform')
    for name in ('admin_a', 'admin_b', 'owner_a1', 'owner_a2', 'owner_b', 'employee', 'plain'):
        identities[name] = str(call({}, 'POST', '/auth/register', {'username': 'qa5_' + name, 'email': 'qa5_' + name + '@example.com', 'password': PASSWORD}, 201)['id'])
    merchants, shops = {}, {}
    for suffix in ('a', 'b'):
        admin = login('admin_' + suffix)
        a = call(admin, 'POST', '/merchants/applications', {'merchant_name': '验收商家' + suffix.upper()}, 201)
        merchants[suffix] = str(call(platform, 'POST', f"/merchants/applications/{a['id']}/review", {'decision': 'APPROVE'})['created_merchant_id'])
    for suffix in ('a1', 'a2', 'b'):
        owner, admin = login('owner_' + suffix), login('admin_' + suffix[0])
        a = call(owner, 'POST', '/shops/applications', {'merchant_id': int(merchants[suffix[0]]), 'shop_name': '验收店铺' + suffix.upper()}, 201)
        shops[suffix] = str(call(admin, 'POST', f"/shops/applications/{a['id']}/review", {'decision': 'APPROVE'})['created_shop_id'])
        a = call(owner, 'POST', '/inventory/access-applications', {}, 201)
        call(admin, 'POST', f"/inventory/access-applications/{a['id']}/review", {'decision': 'APPROVE'})
    a = call(login('employee'), 'POST', '/employees/applications', {'shop_id': int(shops['a1'])}, 201)
    call(login('owner_a1'), 'POST', f"/employees/applications/{a['id']}/review", {'decision': 'APPROVE'})
    admin = login('admin_a')
    category = call(admin, 'POST', '/catalog/categories', {'name': '验收分类'}, 201)
    spu = call(admin, 'POST', '/catalog/spus', {'category_id': category['id'], 'name': '共享订单商品'}, 201)
    skus = [call(admin, 'POST', f"/catalog/spus/{spu['id']}/skus", {'sku_code': 'QA5-' + str(i), 'specification': spec, 'price': price}, 201) for i, spec, price in ((1, '标准', '19.90'), (2, '缺货规格', '2.30'))]
    call(admin, 'PATCH', f"/catalog/spus/{spu['id']}/publish")
    call(admin, 'POST', '/inventory/operations', {'sku_id': skus[0]['id'], 'delta': 12}, key=str(uuid.uuid4()))
    order = call(login('owner_a1'), 'POST', '/orders', {'items': [{'sku_id': skus[0]['id'], 'quantity': 1}]}, 201, str(uuid.uuid4()))
    return {'user_ids': identities, 'merchants': merchants, 'shops': shops, 'spu_id': str(spu['id']), 'sku_id': str(skus[0]['id']), 'sku2_id': str(skus[1]['id']), 'order_id': str(order['id'])}

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def handle_request(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/__qa/stop':
            stop.set(); code, payload = 200, b'{}'
        elif self.path == '/__qa/fault':
            with lock: fault.update(json.loads(body))
            code, payload = 200, b'{}'
        else:
            headers = {k: v for k, v in self.headers.items() if k.lower() in {'authorization', 'content-type', 'idempotency-key'}}
            response = httpx.request(self.command, 'http://127.0.0.1:8019' + self.path, headers=headers, content=body, timeout=25)
            code, payload = response.status_code, response.content
            kind = 'create' if self.path == '/api/v1/orders' else 'complete' if self.path.endswith('/complete') else 'cancel' if self.path.endswith('/cancel') else None
            with lock:
                if self.command == 'POST' and code in (200, 201) and kind and fault.get(kind, 0):
                    fault[kind] -= 1
                    if kind == 'create' and fault.get('settle_create') == 'cancel':
                        oid = response.json()['id']
                        settled = httpx.post(f'{API}/orders/{oid}/cancel', headers=headers, timeout=20)
                        assert settled.status_code == 200
                    code, payload = 503, b'{"detail":"QA: response lost after committed transaction"}'
            requests.append({'method': self.command, 'path': self.path, 'backend_status': response.status_code, 'returned_status': code, 'key': self.headers.get('Idempotency-Key'), 'payload_sha256': hashlib.sha256(body).hexdigest()})
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers(); self.wfile.write(payload)
    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_request

def main():
    for port in (8018, 8019, 6179):
        with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1', port)) != 0, f'Port {port} occupied'
    ws = Workspace('phase5-controller-' + secrets.token_hex(4))
    db = TestDatabase(ws)
    server, engine = None, None
    meta = {'status': 'initializing', 'audit_directory': str(ws.run_dir)}
    try:
        db.start(); db.migrate()
        env = db.environment()
        # Recheck the two trigger fault-injection cases with the controlled local
        # admin connection in THIS new disposable schema; no global flags changed.
        root_settings = Settings()
        retry_env = {**env, 'DB_USER': root_settings.db_user, 'DB_PASSWORD': root_settings.db_password.get_secret_value()}
        retry = run_process([str(BACKEND / '.venv/Scripts/python.exe'), '-m', 'pytest', '-q', '--tb=short', 'tests/test_orders.py::test_order_write_atomicity_and_independent_audit_failure', 'tests/test_phase12.py::test_mysql_database_failure_mid_confirmation_rolls_back_owner_and_employee_rows'], BACKEND, retry_env, 180)
        (ROOT / 'Frontend/PHASE5_TRIGGER_RECHECK.json').write_text(json.dumps({'mode': 'controlled admin account, disposable schema only', 'database': db.name, **retry}, ensure_ascii=False, indent=2), encoding='utf-8')
        assert retry['exit_code'] == 0, 'Trigger recheck failed; see sanitized report'
        engine = create_engine(URL.create('mysql+pymysql', username=db.user, password=db.password, host=db.host, port=db.port, database=db.name), hide_parameters=True)
        with Session(engine) as session, session.begin():
            session.add(User(id=9007199254770001, username='qa5_platform', email='qa5_platform@example.com', password_hash=PasswordHash.recommended().hash(PASSWORD), is_platform_admin=True))
        commands = [([str(BACKEND / '.venv/Scripts/python.exe'), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8019', '--no-access-log'], BACKEND, env), ([shutil.which('node'), str(ROOT / 'Frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '6179', '--strictPort'], ROOT / 'Frontend', {**child_environment(), 'VITE_DEV_PROXY_TARGET': 'http://127.0.0.1:8018'})]
        for i, (cmd, cwd, child_env) in enumerate(commands):
            log = (ws.run_dir / f'app-{i}.log').open('wb'); logs.append(log)
            processes.append(subprocess.Popen(cmd, cwd=cwd, env=child_env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        server = ThreadingHTTPServer(('127.0.0.1', 8018), Proxy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        for _ in range(80):
            try:
                assert httpx.get('http://127.0.0.1:8019/openapi.json', timeout=1).status_code == 200
                assert httpx.get('http://127.0.0.1:6179', timeout=1).status_code == 200
                break
            except (httpx.RequestError, AssertionError): time.sleep(.25)
        else: raise RuntimeError('Readiness failed')
        meta.update(seed())
        with db.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT VERSION()'); meta['mysql_version'] = cursor.fetchone()[0]
            cursor.execute('SELECT version_num FROM alembic_version'); meta['head'] = cursor.fetchone()[0]
            cursor.execute('SHOW TABLES'); meta['business_tables'] = len(cursor.fetchall()) - 1
        meta.update(status='active', db=db.name, temporary_account=db.user, api_url=API, frontend_url='http://127.0.0.1:6179')
        META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        print('READY: isolated fixture initialized, restricted API account; no secrets saved.', flush=True)
        stop.wait()
    finally:
        if server: server.shutdown(); server.server_close()
        for process in reversed(processes): stop_process(process)
        for log in logs: log.close()
        if engine: engine.dispose()
        db.close()
        meta['status'] = 'cleaned' if not db.created and not db.user_created else 'cleanup_failed'
        META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        REPORT.write_text(json.dumps({'proxy_requests': requests, 'cleanup': meta['status'], 'audit_directory': str(ws.run_dir)}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Cleanup:', meta['status'], flush=True)

if __name__ == '__main__': main()
