"""Real HTTP acceptance against phase5_env's owned disposable environment only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid
import httpx
import phase5_env as qa

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
ok('real MySQL 9.7.2 isolated schema, 20 tables, migration head verified')
assert stocks(a1) == stocks(a2)
ok('two authorized shops share exactly the same warehouse and SKU stock')
assert qa.call(a1, 'GET', '/orders/5')['status'] == 'COMPLETED'
assert qa.call(emp, 'GET', '/orders/7')['status'] == 'COMPLETED'
assert qa.call(emp, 'GET', '/orders/4')['status'] == 'CANCELLED'
ok('browser owner and employee completion and cancellation persisted in MySQL')
replay_key = '3cad0c57-5b5e-4416-9e7c-ef7122f453da'
before = quantity()
replayed = post_order(emp, [{'sku_id': sku, 'quantity': 1}], replay_key)
assert replayed['id'] == 6 and replayed['status'] == 'CANCELLED' and quantity() == before
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
for actor in (a2, b, admin_b): qa.call(actor, 'GET', '/orders/7', expected=404)
assert qa.call(admin, 'GET', '/orders/7')['id'] == 7
qa.call(admin, 'POST', '/orders/7/complete', expected=403)
for actor in (plain, platform): qa.call(actor, 'GET', '/orders', expected=403)
ok('cross-shop/cross-merchant details rejected; merchant read-only and NONE/platform boundaries')
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
qa.call(a1, 'POST', '/orders/5/cancel', expected=409)
ok('pending cancellation restores once; repeated cancellation and completed cancellation conflict')
historical = qa.call(a1, 'GET', '/orders/5')
qa.call(admin, 'PATCH', f'/catalog/skus/{sku}', {'price': '29.90'})
app = qa.call(admin, 'POST', '/catalog/offshelf-applications', {'spu_id': int(s['spu_id'])}, 201)
qa.call(platform, 'POST', f"/catalog/offshelf-applications/{app['id']}/review", {'decision': 'APPROVE'})
post_order(a1, [{'sku_id': sku, 'quantity': 1}], expected=409)
after = qa.call(emp, 'GET', '/orders/5')
assert after['items'] == historical['items'] and after['total_amount'] == '19.90'
ok('repricing and approved offshelf preserve historical snapshots; new off-sale orders rejected')
qa.call(a1, 'DELETE', f"/shops/{s['shops']['a1']}/employees/{s['user_ids']['employee']}", expected=204)
assert qa.call(emp, 'GET', '/auth/me')['role'] == 'NONE'
qa.call(emp, 'GET', '/orders/5', expected=404)
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
(qa.ROOT / 'Frontend/PHASE5_ACCEPTANCE.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'{len(checks)} real HTTP acceptance groups passed; two-shop race returned 201/409.')
