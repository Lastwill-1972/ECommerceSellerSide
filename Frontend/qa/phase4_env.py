"""Disposable phase4 acceptance helper. Run with backend/.venv, never production.

Reads DB credentials from existing Settings without printing them. Test passwords
come only from PHASE4_PASSWORD. No JWT or password is saved in artifacts.
"""
import json
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.chdir(ROOT / 'backend')
from sqlalchemy import create_engine, text, inspect
from app.core.config import Settings

ARTIFACT = ROOT / 'Frontend/PHASE4_TEST_FIXTURE.json'
RESULTS = ROOT / 'Frontend/PHASE4_HTTP_RESULTS.json'
BASE = 'http://127.0.0.1:8016/api/v1'

def state():
    data = json.loads(ARTIFACT.read_text(encoding='utf-8'))
    assert re.fullmatch(r'test_frontend_phase4_[0-9a-f]{8}', data['db'])
    return data

def configure():
    data = state()
    os.environ['DB_NAME'] = data['db']
    os.environ['APP_ENV'] = 'test'
    return data

def save(data):
    ARTIFACT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def login(name):
    import httpx
    response = httpx.post(BASE + '/auth/login', json={'identifier': name, 'password': os.environ['PHASE4_PASSWORD']})
    assert response.status_code == 200, ('login', response.status_code)
    return {'Authorization': 'Bearer ' + response.json()['access_token']}

def call(headers, method, path, body=None, expected=200):
    import httpx
    response = httpx.request(method, BASE + path, headers=headers, json=body, timeout=20)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:300])
    return response.json() if response.status_code != 204 else None

def setup():
    if ARTIFACT.exists():
        assert state()['status'] == 'cleaned', 'clean previous owned environment first'
    settings = Settings()
    engine = create_engine(settings.database_url.set(database=None), hide_parameters=True)
    name = 'test_frontend_phase4_' + secrets.token_hex(4)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
    save({'db': name, 'status': 'active', 'api_url': BASE, 'frontend_url': 'http://127.0.0.1:6176'})
    configure()
    from alembic.config import Config
    from alembic import command
    command.upgrade(Config(str(ROOT / 'backend/alembic.ini')), 'head')
    from app.db import models  # noqa: F401
    from app.modules.users.models import User
    from sqlalchemy.orm import Session
    from pwdlib import PasswordHash
    engine.dispose()
    engine = create_engine(Settings().database_url, hide_parameters=True)
    with Session(engine) as session, session.begin():
        session.add(User(id=9007199254760001, username='qa4_platform', email='qa4_platform@example.com', password_hash=PasswordHash.recommended().hash(os.environ['PHASE4_PASSWORD']), is_platform_admin=True))
    with engine.connect() as connection:
        version = connection.execute(text('SELECT VERSION()')).scalar_one()
        head = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        tables = len(inspect(connection).get_table_names()) - 1
    save({**state(), 'mysql_version': version, 'head': head, 'business_tables': tables})
    engine.dispose()
    print('Created isolated test database; migrations and platform test fixture ready.')

def fixture():
    data = configure()
    platform = login('qa4_platform')
    ids = {}
    for name in ('admin_a', 'admin_b', 'owner_a', 'owner_b', 'employee', 'target', 'plain'):
        result = call({}, 'POST', '/auth/register', {'username': 'qa4_' + name, 'email': 'qa4_' + name + '@example.com', 'password': os.environ['PHASE4_PASSWORD']}, 201)
        ids[name] = str(result['id'])
    merchants, shops = {}, {}
    for suffix in ('a', 'b'):
        admin = login('qa4_admin_' + suffix)
        application = call(admin, 'POST', '/merchants/applications', {'merchant_name': '验收商家' + suffix.upper()}, 201)
        result = call(platform, 'POST', f"/merchants/applications/{application['id']}/review", {'decision': 'APPROVE'})
        merchants[suffix] = str(result['created_merchant_id'])
        owner = login('qa4_owner_' + suffix)
        application = call(owner, 'POST', '/shops/applications', {'merchant_id': int(merchants[suffix]), 'shop_name': '验收店铺' + suffix.upper()}, 201)
        result = call(admin, 'POST', f"/shops/applications/{application['id']}/review", {'decision': 'APPROVE'})
        shops[suffix] = str(result['created_shop_id'])
    admin = login('qa4_admin_a')
    cat = call(admin, 'POST', '/catalog/categories', {'name': '验收分类'}, 201)
    spu = call(admin, 'POST', '/catalog/spus', {'category_id': cat['id'], 'name': '验收共享商品'}, 201)
    sku = call(admin, 'POST', f"/catalog/spus/{spu['id']}/skus", {'sku_code': 'QA4-1', 'specification': '标准', 'price': '19.90'}, 201)
    call(admin, 'PATCH', f"/catalog/spus/{spu['id']}/publish")
    save({**data, 'user_ids': ids, 'merchants': merchants, 'shops': shops, 'sku_id': str(sku['id'])})
    print('Two merchants, isolated shops, product and zero stock prepared through real APIs.')

def cleanup():
    data = state()
    assert data['status'] == 'active'
    engine = create_engine(Settings().database_url.set(database=None), hide_parameters=True)
    with engine.begin() as connection:
        connection.execute(text(f"DROP DATABASE `{data['db']}`"))
        assert connection.execute(text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:db'), {'db': data['db']}).scalar_one() == 0
    engine.dispose()
    save({**data, 'status': 'cleaned', 'note': 'Disposable database and all accounts deleted; local services stopped separately. No development data used.'})
    print('Owned disposable database removed; absence verified.')

def checks():
    """Run AFTER browser normal round-trip and FORCE acceptance, before cleanup."""
    s = configure()
    a, b = s['shops']['a'], s['shops']['b']
    owner = login('qa4_target')  # new owner after browser FORCE
    old = login('qa4_owner_a')
    admin_a, admin_b = login('qa4_admin_a'), login('qa4_admin_b')
    employee, plain = login('qa4_employee'), login('qa4_plain')
    platform = login('qa4_platform')
    results = []
    def ok(label): results.append({'check': label, 'result': 'passed'})
    for headers, expected, label in ((old, 'NONE', 'FORCE old owner loses role'), (owner, 'SHOP_OWNER', 'FORCE new owner identity'), (employee, 'SHOP_EMPLOYEE', 'normal handoff restores original employee identity')):
        assert call(headers, 'GET', '/auth/me')['role'] == expected
        ok(label)
    staff = call(owner, 'GET', f'/shops/{a}/employees')['items']
    assert next(row for row in staff if str(row['user_id']) == s['user_ids']['employee'])['role_name'] == '销售专员'
    ok('normal return restores the exact original custom role')
    assert {str(row['id']) for row in call(admin_a, 'GET', '/shops')['items']} == {a}
    assert {str(row['id']) for row in call(admin_b, 'GET', '/shops')['items']} == {b}
    ok('merchant A/B shop lists isolated')
    assert all(str(row['shop_id']) == a for row in call(owner, 'GET', '/employees/applications')['items'])
    ok('owner employee applications scoped to own shop')
    call(owner, 'GET', f'/shops/{b}/employees', expected=403)
    call(owner, 'GET', f'/shops/{b}/employee-roles', expected=403)
    call(admin_a, 'PUT', f'/shops/{b}/owner', {'action': 'FORCE', 'target_user_id': int(s['user_ids']['plain']), 'reason': 'cross tenant denied'}, 403)
    ok('cross-shop employee/role access and cross-merchant handoff rejected')
    role_b = call(login('qa4_owner_b'), 'GET', f'/shops/{b}/employee-roles')['items'][0]['id']
    call(owner, 'PATCH', f"/shops/{a}/employees/{s['user_ids']['employee']}/role", {'role_id': role_b}, 404)
    ok('foreign shop role assignment rejected')
    call(employee, 'GET', f'/shops/{a}/employees', expected=403)
    call(platform, 'GET', f'/shops/{a}/employees', expected=403)
    ok('employee and platform cannot manage shop team')
    call({**owner, 'Idempotency-Key': '12345678-1234-4234-8234-123456789abc'}, 'POST', '/inventory/operations', {'sku_id': int(s['sku_id']), 'delta': 1}, 403)
    call(owner, 'POST', '/catalog/categories', {'name': 'not allowed'}, 403)
    ok('owner product and manual inventory writes forbidden')
    assert call(owner, 'GET', '/inventory/stocks')['items'][0]['quantity'] == 0
    call(owner, 'POST', '/inventory/access-applications', {}, 409)
    ok('warehouse grant survives handoff and prevents duplicate grant request')
    call(admin_a, 'PUT', f'/shops/{a}/owner', {'action': 'CONFIRM', 'proposal_id': '12345678-1234-4234-8234-123456789abc'}, 409)
    call(admin_a, 'PUT', f'/shops/{a}/owner', {'action': 'CONFIRM', 'proposal_id': 'invalid'}, 422)
    call(admin_a, 'PUT', f'/shops/{a}/owner', {'action': 'FORCE', 'target_user_id': int(s['user_ids']['plain']), 'reason': '  '}, 422)
    ok('invalid or stale proposal and blank FORCE reason fail 409/422')
    call(owner, 'POST', f'/shops/{a}/employee-roles', {'name': '销售专员'}, 409)
    call(owner, 'POST', f'/shops/{a}/employee-roles', {'name': 'x' * 101}, 422)
    ok('role uniqueness and schema length failures 409/422')
    foreign_app = next(row for row in call(admin_b, 'GET', '/shops/applications')['items'] if row['shop_name'] == '浏览器开店申请')
    call(admin_a, 'POST', f"/shops/applications/{foreign_app['id']}/review", {'decision': 'APPROVE'}, 403)
    ok('merchant A cannot review merchant B application')
    application = call(plain, 'POST', '/employees/applications', {'shop_id': int(a)}, 201)
    call(plain, 'POST', '/employees/applications', {'shop_id': int(b)}, 409)
    call(owner, 'POST', f"/employees/applications/{application['id']}/review", {'decision': 'REJECT', 'rejection_reason': '验收拒绝'}, 200)
    call(owner, 'POST', f"/employees/applications/{application['id']}/review", {'decision': 'APPROVE'}, 409)
    ok('single pending employee application, rejection and duplicate review guarded')
    call(owner, 'DELETE', f"/shops/{a}/employees/{s['user_ids']['employee']}", expected=204)
    assert call(employee, 'GET', '/auth/me')['role'] == 'NONE'
    call(employee, 'GET', '/inventory/stocks', expected=403)
    ok('204 removal preserves user, old JWT immediately loses employee access')
    application = call(employee, 'POST', '/employees/applications', {'shop_id': int(a)}, 201)
    call(owner, 'POST', f"/employees/applications/{application['id']}/review", {'decision': 'APPROVE'})
    assert call(employee, 'GET', '/auth/me')['role'] == 'SHOP_EMPLOYEE'
    ok('removed employee reapplies normally and is authorized from current relation')
    call({}, 'GET', '/auth/me', expected=401)
    call({'Authorization': 'Bearer invalid-token'}, 'GET', '/auth/me', expected=401)
    ok('missing and illegal token rejected 401')
    engine = create_engine(Settings().database_url, hide_parameters=True)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261017_phase12'
        assert len(inspect(connection).get_table_names()) == 21
        assert connection.execute(text('SELECT COUNT(*) FROM warehouse_accesses WHERE shop_id=:id'), {'id': int(a)}).scalar_one() == 1
        assert connection.execute(text('SELECT COUNT(*) FROM audit_events WHERE event_type LIKE :pattern'), {'pattern': 'shop_owner_transfer%'}).scalar_one() >= 5
    engine.dispose()
    ok('real MySQL head, 20 tables, preserved warehouse grant and transfer audit events')
    import httpx
    actual = httpx.get('http://127.0.0.1:8016/openapi.json').json()
    snapshot = json.loads((ROOT / 'Frontend/PHASE4_OPENAPI.json').read_text(encoding='utf-8'))
    assert actual == snapshot
    ok('running FastAPI OpenAPI equals checked phase4 contract')
    RESULTS.write_text(json.dumps({'status': 'passed', 'mysql_version': s['mysql_version'], 'head': s['head'], 'checks': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(results)} HTTP/database acceptance groups passed; no credentials persisted.')

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'setup': setup()
    elif mode == 'fixture': fixture()
    elif mode == 'cleanup': cleanup()
    elif mode == 'checks': checks()
    elif mode == 'serve':
        configure()
        os.environ['AUTH_JWT_SECRET'] = secrets.token_urlsafe(48)
        import uvicorn
        uvicorn.run('app.main:app', host='127.0.0.1', port=8016, log_level='warning', access_log=False)
