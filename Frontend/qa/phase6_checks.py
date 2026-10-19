"""Real HTTP acceptance against phase6_env's owned disposable environment only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid
import httpx
import phase6_env as qa

s = json.loads(qa.META.read_text(encoding='utf-8'))
assert s['status'] == 'active' and s['db'].startswith('test_deepseek_')
checks = []
def ok(name): checks.append({'check': name, 'result': 'passed'})
a1, a2, b = qa.login('owner_a1'), qa.login('owner_a2'), qa.login('owner_b')
emp, admin, admin_b, platform, plain = [qa.login(name) for name in ('employee', 'admin_a', 'admin_b', 'platform', 'plain')]
sku = int(s['sku_id']); sku2 = int(s['sku2_id'])
def stocks(headers): return qa.call(headers, 'GET', '/inventory/stocks')['items']
def quantity(): return next(row['quantity'] for row in stocks(a1) if row['sku_id'] == sku)
def post_order(headers, items, key=None, expected=201): return qa.call(headers, 'POST', '/orders', {'items': items}, expected, key or str(uuid.uuid4()))
assert s['head'] == '20261017_phase12' and s['business_tables'] == 20
ok('real MySQL isolated schema, 20 tables, migration head verified')
assert stocks(a1) == stocks(a2)
ok('two authorized shops share exactly the same warehouse and SKU stock')
complete_order = post_order(a1, [{'sku_id': sku, 'quantity': 1}])
complete_id = complete_order['id']
complete_before = quantity()
httpx.post('http://127.0.0.1:8018/__qa/fault', json={'complete': 1}).raise_for_status()
assert httpx.post(f'http://127.0.0.1:8018/api/v1/orders/{complete_id}/complete', headers=a1, json={}, timeout=20).status_code == 503
assert qa.call(a1, 'GET', f'/orders/{complete_id}')['status'] == 'COMPLETED'
assert quantity() == complete_before
cancel_order = post_order(emp, [{'sku_id': sku, 'quantity': 1}])
cancel_id = cancel_order['id']
httpx.post('http://127.0.0.1:8018/__qa/fault', json={'cancel': 1}).raise_for_status()
assert httpx.post(f'http://127.0.0.1:8018/api/v1/orders/{cancel_id}/cancel', headers=emp, json={}, timeout=20).status_code == 503
assert qa.call(emp, 'GET', f'/orders/{cancel_id}')['status'] == 'CANCELLED'
ok('complete does not deduct twice; employee cancellation restores stock')
replay_key = str(uuid.uuid4())
replay_order = post_order(emp, [{'sku_id': sku, 'quantity': 1}], replay_key)
qa.call(emp, 'POST', f"/orders/{replay_order['id']}/cancel")
before = quantity()
replayed = post_order(emp, [{'sku_id': sku, 'quantity': 1}], replay_key)
assert replayed['id'] == replay_order['id'] and replayed['status'] == 'CANCELLED' and quantity() == before
post_order(emp, [{'sku_id': sku, 'quantity': 2}], replay_key, 409)
post_order(a1, [{'sku_id': sku, 'quantity': 1}], replay_key, 409)
ok('original key replays cancelled order without rededuction; changed payload/actor rejected')
before = quantity()
total = qa.call(a1, 'GET', '/orders')['total']
post_order(a1, [{'sku_id': sku, 'quantity': 1}, {'sku_id': sku2, 'quantity': 1}], expected=409)
assert quantity() == before and qa.call(a1, 'GET', '/orders')['total'] == total
ok('multi-SKU shortage rolls back whole order and all stock deductions')
post_order(a1, [{'sku_id': sku, 'quantity': 0}], expected=422)
post_order(a1, [{'sku_id': str(sku), 'quantity': 1}], expected=422)
ok('real strict integer schema rejects zero quantity and JSON string SKU id')
for actor in (a2, b, admin_b): qa.call(actor, 'GET', f'/orders/{complete_id}', expected=404)
assert qa.call(admin, 'GET', f'/orders/{complete_id}')['id'] == complete_id
qa.call(admin, 'POST', f'/orders/{complete_id}/complete', expected=403)
for actor in (plain, platform): qa.call(actor, 'GET', '/orders', expected=403)
ok('cross-shop/cross-merchant details rejected; merchant read-only and NONE/platform boundaries')

# Exercise every fixed identity against real forbidden API calls.
for actor in (plain, platform):
    for path in ('/shops', '/catalog/categories', '/inventory/warehouse', '/orders'):
        qa.call(actor, 'GET', path, expected=403)
for actor in (a1, a2, emp):
    qa.call(actor, 'POST', '/inventory/operations', {'sku_id': sku, 'delta': 1}, expected=403, key=str(uuid.uuid4()))
for actor in (emp, plain): qa.call(actor, 'GET', '/audit/events', expected=403)
qa.call(platform, 'GET', '/catalog/spus', expected=403)
qa.call(admin, 'POST', f'/orders/{complete_id}/cancel', expected=403)
qa.call(a2, 'GET', f'/shops/{s["shops"]["a1"]}/employees', expected=403)
qa.call(emp, 'GET', f'/shops/{s["shops"]["a1"]}/employees', expected=403)
ok('five fixed identities enforce backend resource and write boundaries')
role = qa.call(a1, 'POST', f'/shops/{s["shops"]["a1"]}/employee-roles', {'name': '平台超级管理员展示岗位'}, 201)
qa.call(a1, 'PATCH', f'/shops/{s["shops"]["a1"]}/employees/{s["user_ids"]["employee"]}/role', {'role_id': role['id']})
qa.call(emp, 'POST', '/catalog/categories', {'name': 'forbidden'}, expected=403)
qa.call(emp, 'POST', '/inventory/operations', {'sku_id': sku, 'delta': 1}, expected=403, key=str(uuid.uuid4()))
ok('custom employee job title does not grant management permissions')
b_category = qa.call(admin_b, 'POST', '/catalog/categories', {'name': '商家B独立分类'}, 201)
assert b_category['id'] not in {r['id'] for r in qa.call(admin, 'GET', '/catalog/categories')['items']}
qa.call(admin_b, 'GET', f'/catalog/spus/{s["spu_id"]}', expected=404)
qa.call(admin_b, 'PATCH', f'/catalog/skus/{sku}', {'price': '0.01'}, expected=404)
a_events = qa.call(admin, 'GET', '/audit/events', expected=200)
b_events = qa.call(admin_b, 'GET', '/audit/events', expected=200)
assert {r['id'] for r in a_events['items']}.isdisjoint({r['id'] for r in b_events['items']})
assert all(r['target_id'] != b_category['id'] for r in a_events['items'] if r['target_type']=='category')
qa.call(a1, 'GET', '/audit/events?event_type=order_created&target_type=order&start_at=2020-01-01T00:00:00&end_at=2099-01-01T00:00:00')
qa.call(a1, 'GET', '/audit/events?start_at=2026-10-19T00:00:00Z', expected=422)
ok('merchant catalog and audit scope isolation plus naive time filters')

if quantity() != 1:
    qa.call(admin, 'POST', '/inventory/operations', {'sku_id': sku, 'delta': 1 - quantity()}, key=str(uuid.uuid4()))
def compete(headers):
    response = httpx.post(qa.API + '/orders', headers={**headers, 'Idempotency-Key': str(uuid.uuid4())}, json={'items': [{'sku_id': sku, 'quantity': 1}]}, timeout=20)
    return headers, response.status_code, response.json()
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(compete, [a1, a2]))
assert sorted(row[1] for row in results) == [201, 409] and quantity() == 0
ok('two real shops compete for one unit: one order succeeds, other conflicts, no oversell')
winner, _, order = next(row for row in results if row[1] == 201)
qa.call(winner, 'POST', f"/orders/{order['id']}/cancel")
assert quantity() == 1
qa.call(winner, 'POST', f"/orders/{order['id']}/cancel", expected=409)
assert quantity() == 1
qa.call(a1, 'POST', f'/orders/{complete_id}/cancel', expected=409)
ok('pending cancellation restores once; repeated cancellation and completed cancellation conflict')
historical = qa.call(a1, 'GET', f'/orders/{complete_id}')
qa.call(admin, 'PATCH', f'/catalog/skus/{sku}', {'price': '29.90'})
app = qa.call(admin, 'POST', '/catalog/offshelf-applications', {'spu_id': int(s['spu_id'])}, 201)
qa.call(platform, 'POST', f"/catalog/offshelf-applications/{app['id']}/review", {'decision': 'APPROVE'})
post_order(a1, [{'sku_id': sku, 'quantity': 1}], expected=409)
after = qa.call(emp, 'GET', f'/orders/{complete_id}')
assert after['items'] == historical['items'] and after['total_amount'] == '19.90'
ok('repricing and approved offshelf preserve historical snapshots; new off-sale orders rejected')
qa.call(a1, 'DELETE', f"/shops/{s['shops']['a1']}/employees/{s['user_ids']['employee']}", expected=204)
assert qa.call(emp, 'GET', '/auth/me')['role'] == 'NONE'
qa.call(emp, 'GET', f'/orders/{complete_id}', expected=404)
ok('removed employee old JWT immediately loses order access')
application = qa.call(emp, 'POST', '/employees/applications', {'shop_id': int(s['shops']['a1'])}, 201)
qa.call(a1, 'POST', f"/employees/applications/{application['id']}/review", {'decision': 'APPROVE'})
assert qa.call(emp, 'GET', '/auth/me')['role'] == 'SHOP_EMPLOYEE'
ok('removed employee can reapply and recover only the new valid relationship')
proposal = qa.call(a2, 'PUT', f"/shops/{s['shops']['a2']}/owner", {'action': 'PROPOSE', 'target_user_id': int(s['user_ids']['plain'])})
qa.call(admin, 'PUT', f"/shops/{s['shops']['a2']}/owner", {'action': 'CONFIRM', 'proposal_id': proposal['proposal_id']})
assert qa.call(a2, 'GET', '/auth/me')['role'] == 'NONE'
qa.call(a2, 'GET', '/orders', expected=403)
assert qa.call(plain, 'GET', '/auth/me')['role'] == 'SHOP_OWNER'
assert stocks(plain) == stocks(a1)
ok('owner handoff revokes old JWT business access and preserves shop warehouse grant')
result = {'status': 'passed', 'database': s['db'], 'checks': checks, 'concurrency_statuses': sorted(row[1] for row in results), 'replay_key': replay_key}
(qa.ROOT / 'Frontend/PHASE6_ACCEPTANCE.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{len(checks)} real HTTP acceptance groups passed; two-shop race returned 201/409.')
