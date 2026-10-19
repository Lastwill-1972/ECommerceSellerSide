import asyncio,json,os
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
import phase6_browser_extra as q
r={'checks':[],'pageerrors':[]}
def api_user(name):
 v=httpx.post('http://127.0.0.1:8019/api/v1/auth/login',json={'identifier':'qa6_'+name,'password':os.environ['PHASE6_PASSWORD']}).json(); return {'Authorization':'Bearer '+v['access_token']}
def restore_emp():
 owner=api_user('owner_a1'); emp=api_user('employee'); base='http://127.0.0.1:8019/api/v1'
 if httpx.get(base+'/auth/me',headers=emp).json()['role']=='NONE':
  a=httpx.post(base+'/employees/applications',headers=emp,json={'shop_id':int(q.m['shops']['a1'])}); assert a.status_code==201
  v=httpx.post(base+f"/employees/applications/{a.json()['id']}/review",headers=owner,json={'decision':'APPROVE'}); assert v.status_code==200
async def main():
 restore_emp()
 async with async_playwright() as pw:
  b=await pw.chromium.launch(headless=True)
  try:
   c,p=await q.login(b,'employee'); p.on('pageerror',lambda e:r['pageerrors'].append(str(e))); ready=asyncio.Event(); release=asyncio.Event()
   async def hold(route):
    response=await route.fetch(); ready.set(); await release.wait(); await route.fulfill(response=response)
   await p.route('**/api/v1/orders?*',hold)
   await p.goto(q.BASE+'/employee/orders',wait_until='domcontentloaded'); await asyncio.wait_for(ready.wait(),10)
   owner=api_user('owner_a1'); empid=q.m['user_ids']['employee']; shop=q.m['shops']['a1']
   response=httpx.delete(f'http://127.0.0.1:8019/api/v1/shops/{shop}/employees/{empid}',headers=owner); assert response.status_code==204
   await p.locator('#mobile-page-select').select_option('employee-catalog'); await p.wait_for_url('**/403'); release.set(); await p.wait_for_load_state('networkidle'); assert await p.locator('.merchant-table').count()==0; await p.screenshot(path=str(q.ART/'identity-change-stale-response.png'),full_page=True); r['checks'].append('removed employee old JWT navigates to 403; late real order response does not restore old data'); await c.close()
   emp=api_user('employee'); application=httpx.post('http://127.0.0.1:8019/api/v1/employees/applications',headers=emp,json={'shop_id':int(shop)}); assert application.status_code==201; result=httpx.post(f"http://127.0.0.1:8019/api/v1/employees/applications/{application.json()['id']}/review",headers=owner,json={'decision':'APPROVE'}); assert result.status_code==200
   c=await b.new_context(viewport={'width':390,'height':844}); p=await c.new_page(); await p.goto(q.BASE+'/login'); await p.evaluate("sessionStorage.setItem('seller_access_token','qa-invalid-token')"); await p.goto(q.BASE+'/employee/orders'); await p.wait_for_url('**/login?**'); assert 'expired' in p.url; await p.screenshot(path=str(q.ART/'login-expired-mobile.png'),full_page=True); r['checks'].append('real invalid JWT 401 redirects protected navigation to expired login'); await c.close()
  finally:
   await b.close(); (q.ROOT/'Frontend/PHASE6_IDENTITY_BROWSER.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':asyncio.run(main())
