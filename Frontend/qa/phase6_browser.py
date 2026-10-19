"""Controlled Chromium desktop/mobile acceptance; credentials remain environment-only."""
import asyncio, json, os
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2]
META=json.loads((ROOT/'Frontend/PHASE6_TEST_FIXTURE.json').read_text(encoding='utf-8'))
assert META['status']=='active' and META['db'].startswith('test_deepseek_')
BASE='http://127.0.0.1:6179'
PASSWORD=os.environ['PHASE6_PASSWORD']
REPORT={'engine':'Chromium','viewports':[],'checks':[],'console':[],'screenshots':[]}
ART=ROOT/'Frontend/docs/phase6'; ART.mkdir(parents=True,exist_ok=True)
async def main():
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(headless=True)
  try:
   for role,username,routes in (
    ('merchant','qa6_admin_a',['/merchant/profile','/merchant/shops','/merchant/applications','/merchant/catalog','/merchant/inventory','/merchant/orders','/merchant/audit']),
    ('owner','qa6_owner_a1',['/shop/employees','/shop/applications','/shop/roles','/shop/warehouse','/shop/catalog','/shop/inventory','/shop/orders','/shop/orders/create','/shop/orders/'+META['order_id'],'/shop/audit']),
    ('employee','qa6_employee',['/employee','/employee/catalog','/employee/inventory','/employee/orders','/employee/orders/create','/employee/orders/'+META['order_id']]),
    ('none','qa6_plain',['/applications']),
   ):
    context=await browser.new_context(viewport={'width':1440,'height':1000})
    page=await context.new_page()
    page.on('console',lambda m: REPORT['console'].append({'type':m.type,'text':m.text}) if m.type in ('warning','error') and ('[Vue' in m.text or 'Unhandled' in m.text) else None)
    page.on('pageerror',lambda e: REPORT['console'].append({'type':'pageerror','text':str(e)}))
    await page.goto(BASE+'/login'); await page.locator('input[autocomplete=username]').fill(username); await page.locator('input[type=password]').fill(PASSWORD); await page.locator('.auth-submit').click(); await page.wait_for_url(BASE+'/')
    for width,height in ((1440,1000),(390,844)):
     await page.set_viewport_size({'width':width,'height':height})
     for route in routes:
      await page.goto(BASE+route); await page.wait_for_load_state('networkidle')
      assert '/403' not in page.url and '/login' not in page.url,(role,route)
      if route.endswith('/audit'): await page.get_by_role('button',name='查询',exact=True).click(); await page.wait_for_load_state('networkidle')
      measurements=await page.evaluate('({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth})')
      REPORT['viewports'].append({'role':role,'route':route,'width':width,'height':height,'document_overflow':measurements['scrollWidth']-measurements['width']})
      if width==390:
       assert await page.locator('#mobile-page-select').is_visible()
      if route.endswith('/audit') or (width==390 and route.endswith('/orders/create')) or (role=='none' and width==390):
       name=f'{role}-{route.split("/")[-1]}-{width}.png'; await page.screenshot(path=str(ART/name),full_page=True,animations='disabled'); REPORT['screenshots'].append('docs/phase6/'+name)
    if role=='merchant':
     await page.set_viewport_size({'width':390,'height':844}); await page.goto(BASE+'/merchant/inventory'); await page.wait_for_load_state('networkidle')
     await page.locator('.operation-form .el-select').click(); await page.get_by_role('option').filter(has_text='QA6-1').click(); await page.get_by_placeholder('例如 10 或 -3').fill('3')
     httpx.post('http://127.0.0.1:8018/__qa/fault',json={'inventory':1}).raise_for_status()
     await page.get_by_role('button',name='提交调整',exact=True).click(); await page.get_by_text('上一笔库存操作结果未知，当前已锁住新操作。',exact=True).wait_for()
     await page.screenshot(path=str(ART/'inventory-unknown-mobile.png'),full_page=True,animations='disabled')
     await page.reload(); await page.get_by_text('上一笔库存操作结果未知，当前已锁住新操作。',exact=True).wait_for()
     await page.get_by_role('button',name='我已确认，使用同一幂等键重试',exact=True).click(); await page.get_by_text('最近一笔库存操作已确认。',exact=True).wait_for()
     REPORT['checks'].append('inventory successful response lost; reload preserves original key; explicit retry confirms')
     await page.screenshot(path=str(ART/'inventory-retry-mobile.png'),full_page=True,animations='disabled')
    if role=='employee':
     await page.set_viewport_size({'width':390,'height':844}); await page.goto(BASE+'/employee/orders/create'); await page.wait_for_load_state('networkidle')
     await page.get_by_role('button',name='查看 SKU',exact=True).first.click(); await page.locator('label.el-checkbox').first.click(); await page.get_by_role('button',name='加入购物清单',exact=True).click()
     httpx.post('http://127.0.0.1:8018/__qa/fault',json={'create':1}).raise_for_status()
     await page.get_by_role('button',name='保存并提交订单',exact=True).click(); await page.wait_for_load_state('networkidle')
     REPORT['checks'].append('employee create response loss triggered; pending UI inspected')
     await page.screenshot(path=str(ART/'order-unknown-mobile.png'),full_page=True,animations='disabled')
     REPORT['order_unknown_text']=(await page.locator('.page-content').inner_text())[-4500:]
    await context.close()
   # Login/register and forbidden routes use the same independent mobile browser.
   context=await browser.new_context(viewport={'width':390,'height':844}); page=await context.new_page()
   for path in ('/login','/register'):
    await page.goto(BASE+path); await page.screenshot(path=str(ART/(path[1:]+'-mobile.png')),full_page=True,animations='disabled')
   await context.close()
  finally:
   await browser.close()
   (ROOT/'Frontend/PHASE6_BROWSER.json').write_text(json.dumps(REPORT,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': asyncio.run(main())
