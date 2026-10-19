import asyncio,json,os
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
root=Path(__file__).resolve().parents[2];m=json.loads((root/'Frontend/PHASE6_MODAL_FIXTURE.json').read_text(encoding='utf-8'));assert m['status']=='active';base='http://127.0.0.1:8019/api/v1';pwd=os.environ['PHASE6_PASSWORD'];art=root/'Frontend/docs/phase6';r={}
def login(user):return {'Authorization':'Bearer '+httpx.post(base+'/auth/login',json={'identifier':'qa6_'+user,'password':pwd}).json()['access_token']}
admin=login('admin_a');plain=login('plain')
new=httpx.post(base+'/auth/register',json={'username':'qa6_modal_owner','email':'qa6_modal_owner@example.com','password':pwd});assert new.status_code==201
owner=login('modal_owner');a=httpx.post(base+'/shops/applications',headers=owner,json={'merchant_id':int(m['merchants']['a']),'shop_name':'移动弹窗验收店'});assert a.status_code==201
v=httpx.post(base+f"/shops/applications/{a.json()['id']}/review",headers=admin,json={'decision':'APPROVE'});assert v.status_code==200
w=httpx.post(base+'/inventory/access-applications',headers=owner,json={});assert w.status_code==201
p=httpx.post(base+'/shops/applications',headers=plain,json={'merchant_id':int(m['merchants']['a']),'shop_name':'驳回原因演示申请'});assert p.status_code==201
category=httpx.get(base+'/catalog/categories',headers=admin).json()['items'][0]
spu=httpx.post(base+'/catalog/spus',headers=admin,json={'category_id':category['id'],'name':'超长商品名称用于移动端展示检查'*14});assert spu.status_code==201
sku=httpx.post(base+f"/catalog/spus/{spu.json()['id']}/skus",headers=admin,json={'sku_code':'LONG-SKU-'+'X'*90,'specification':'超长规格文字 '*90,'price':'9999999999.99'});assert sku.status_code==201
async def main():
 async with async_playwright() as pw:
  b=await pw.chromium.launch(headless=True)
  try:
   c=await b.new_context(viewport={'width':390,'height':844});page=await c.new_page();await page.goto('http://127.0.0.1:6179/login');await page.locator('input[autocomplete=username]').fill('qa6_admin_a');await page.locator('input[type=password]').fill(pwd);await page.locator('.auth-submit').click();await page.wait_for_url('http://127.0.0.1:6179/')
   await page.goto('http://127.0.0.1:6179/merchant/applications');await page.get_by_role('button',name='仓库授权申请',exact=True).click();await page.get_by_role('button',name='批准',exact=True).first.click();d=page.locator('.el-dialog:visible');await d.wait_for();await page.screenshot(path=str(art/'warehouse-approve-dialog-mobile.png'),full_page=True,animations='disabled');box=await d.bounding_box();assert box['x']>=0 and box['x']+box['width']<=391;assert await d.evaluate('(e)=>getComputedStyle(e).opacity')=='1';r['warehouse_dialog_box']=box
   assert '不等于任意库存修改权' in await d.inner_text();await d.get_by_role('button',name='确认批准',exact=True).click();await d.wait_for(state='hidden');assert httpx.get(base+'/inventory/warehouse',headers=owner).status_code==200;r['warehouse_approval']='passed: real access granted'
   await page.get_by_role('button',name='店铺申请',exact=True).click();await page.get_by_role('button',name='拒绝',exact=True).first.click();d=page.locator('.el-dialog:visible');await d.wait_for();assert await d.get_by_role('button',name='确认拒绝',exact=True).is_disabled();await d.get_by_placeholder('请输入原因').fill('移动端原因验证');await page.screenshot(path=str(art/'warehouse-reject-dialog-mobile.png'),full_page=True,animations='disabled');r['rejection_dialog_box']=await d.bounding_box();await d.get_by_role('button',name='确认拒绝',exact=True).click();await d.wait_for(state='hidden');r['rejection']='passed: required reason and actual reject response'
   await page.goto('http://127.0.0.1:6179/merchant/catalog');await page.wait_for_load_state('networkidle');await page.locator('.el-table__row').filter(has_text='超长商品名称').get_by_role('button',name='详情',exact=True).click();d=page.locator('.el-dialog:visible');await d.get_by_text('¥ 9,999,999,999.99',exact=True).wait_for();await page.screenshot(path=str(art/'long-sku-price-mobile.png'),full_page=True,animations='disabled');r['long_dialog_box']=await d.bounding_box();r['long_dialog_opacity']=await d.evaluate('(e)=>getComputedStyle(e).opacity');assert r['long_dialog_opacity']=='1'
  finally:await b.close();(root/'Frontend/PHASE6_MODAL_VERIFICATION.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
if __name__=='__main__':asyncio.run(main())
