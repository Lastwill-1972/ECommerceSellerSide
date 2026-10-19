import asyncio,json,os
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2]; ART=ROOT/'Frontend/docs/phase6'; BASE='http://127.0.0.1:6179'; PASSWORD=os.environ['PHASE6_PASSWORD']
m=json.loads((ROOT/'Frontend/PHASE6_TEST_FIXTURE.json').read_text(encoding='utf-8')); assert m['status']=='active'
r={'checks':[],'console':[],'order_posts':[]}
async def login(browser,user):
 c=await browser.new_context(viewport={'width':390,'height':844}); p=await c.new_page()
 p.on('pageerror',lambda e:r['console'].append(str(e)))
 await p.goto(BASE+'/login'); await p.locator('input[autocomplete=username]').fill('qa6_'+user); await p.locator('input[type=password]').fill(PASSWORD); await p.locator('.auth-submit').click(); await p.wait_for_url(BASE+'/'); return c,p
async def main():
 async with async_playwright() as pw:
  b=await pw.chromium.launch(headless=True)
  try:
   for user,paths in (('plain',['/merchant/audit','/shop/orders','/employee/orders']),('employee',['/merchant/audit','/shop/employees']),('platform',['/merchant/audit','/shop/audit']),('admin_a',['/shop/orders'])):
    c,p=await login(b,user)
    for path in paths:
     await p.goto(BASE+path); await p.wait_for_url('**/403'); assert await p.get_by_role('heading').count()>0
     r['checks'].append(user+' forbidden '+path)
    await c.close()
   c,p=await login(b,'ungranted'); await p.goto(BASE+'/shop/inventory'); await p.wait_for_load_state('networkidle'); assert '/403' not in p.url; assert '授权' in await p.locator('.page-content').inner_text(); await p.screenshot(path=str(ART/'warehouse-not-authorized-mobile.png'),full_page=True,animations='disabled'); r['checks'].append('warehouse resource 403 remains on shop inventory page'); await c.close()
   c,p=await login(b,'admin_a'); await p.goto(BASE+'/merchant/applications'); await p.get_by_role('button',name='仓库授权申请',exact=True).click(); await p.get_by_role('button',name='批准',exact=True).first.click(); d=p.get_by_role('dialog'); await d.wait_for(); assert '不等于任意库存修改权' in await d.inner_text(); rect=await d.bounding_box(); assert rect['width']<=390 and rect['x']>=0; await p.screenshot(path=str(ART/'warehouse-approve-dialog-mobile.png'),full_page=True,animations='disabled'); await d.get_by_role('button',name='取消',exact=True).click();
   await p.get_by_role('button',name='拒绝',exact=True).first.click(); d=p.get_by_role('dialog'); await d.wait_for(); assert await d.get_by_role('button',name='确认拒绝',exact=True).is_disabled(); await d.get_by_placeholder('请输入原因').fill('移动端审批原因演示'); assert await d.get_by_role('button',name='确认拒绝',exact=True).is_enabled(); await p.screenshot(path=str(ART/'warehouse-reject-dialog-mobile.png'),full_page=True,animations='disabled'); await d.get_by_role('button',name='取消',exact=True).click(); r['checks'].append('mobile approval/rejection dialogs fit viewport and validate reason')
   await p.goto(BASE+'/merchant/catalog'); await p.locator('.el-table__row').filter(has_text='超长商品名称').get_by_role('button',name='详情',exact=True).click(); await p.get_by_role('dialog').get_by_text('¥ 9,999,999,999.99',exact=True).wait_for(); await p.screenshot(path=str(ART/'long-sku-price-mobile.png'),full_page=True,animations='disabled'); r['checks'].append('long product/SKU and max catalog price rendered in mobile detail'); await c.close()
   c,p=await login(b,'employee')
   p.on('request',lambda req:r['order_posts'].append({'url':req.url,'key':req.headers.get('idempotency-key')}) if req.method=='POST' and req.url.endswith('/api/v1/orders') else None)
   await p.goto(BASE+'/employee/orders/create'); await p.get_by_role('button',name='查看 SKU',exact=True).first.click(); await p.locator('label.el-checkbox').first.click(); await p.get_by_role('button',name='加入购物清单',exact=True).click()
   httpx.post('http://127.0.0.1:8018/__qa/fault',json={'create':1}).raise_for_status()
   await p.get_by_role('button',name='保存并提交订单',exact=True).click(); await p.locator('.pending-card').wait_for(); await p.wait_for_load_state('networkidle'); assert len(r['order_posts'])==1
   # Fault injection: remove ONLY the order record, never the JWT. Reload must not auto-POST.
   await p.evaluate("for (const k of Object.keys(sessionStorage)) if(k.startsWith('seller.pending-order.v1:')) sessionStorage.removeItem(k)")
   await p.reload(); await p.wait_for_load_state('networkidle'); assert len(r['order_posts'])==1; assert '记录丢失无法自动证明请求失败' in await p.locator('.page-content').inner_text(); await p.screenshot(path=str(ART/'order-record-lost-mobile.png'),full_page=True,animations='disabled'); r['checks'].append('lost request record reload issues no new POST and shows manual reconciliation warning'); await c.close()
  finally:
   await b.close(); (ROOT/'Frontend/PHASE6_BROWSER_EXTRA.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':asyncio.run(main())
