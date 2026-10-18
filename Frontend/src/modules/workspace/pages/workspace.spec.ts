import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
import { ApiError } from '@/shared/http/errors'
import { ElButton, messageBoxConfirm, elementMessages } from '@/modules/merchant/test-support/elementPlus'
import ApplicationsPage from './ApplicationsPage.vue'
import ShopPeoplePage from './ShopPeoplePage.vue'
import ShopReadPage from './ShopReadPage.vue'
import OwnerTransferPage from './OwnerTransferPage.vue'
import type { IdentityRole } from '@/shared/types/api'

const api = vi.hoisted(() => ({ directory: vi.fn(), merchantApplications: vi.fn(), applyMerchant: vi.fn(), applyShop: vi.fn(), employeeApplications: vi.fn(), applyEmployee: vi.fn(), employees: vi.fn(), roles: vi.fn(), createRole: vi.fn(), assignRole: vi.fn(), removeEmployee: vi.fn(), reviewEmployee: vi.fn(), applyWarehouse: vi.fn(), transferOwner: vi.fn() }))
const merchant = vi.hoisted(() => ({ listShopApplications: vi.fn(), listShops: vi.fn(), listWarehouseApplications: vi.fn() }))
const inventory = vi.hoisted(() => ({ getWarehouse: vi.fn(), listStocks: vi.fn() }))
const catalog = vi.hoisted(() => ({ listCategories: vi.fn(), listSpus: vi.fn(), getSpu: vi.fn() }))
vi.mock('../api', async importOriginal => ({ ...await importOriginal<typeof import('../api')>(), ...api, __v_isRef: false }))
vi.mock('@/modules/merchant/api', () => merchant)
vi.mock('@/modules/merchant/inventoryApi', () => inventory)
vi.mock('@/modules/merchant/catalogApi', () => catalog)
vi.mock('element-plus', async () => (await import('@/modules/merchant/test-support/elementPlus')).elementPlusMock)

const page = <T>(items: T[], total = items.length, offset = 0) => ({ items, total, offset, limit: 20 })
const employee = { id: '6', shop_id: '30', user_id: '7', role_id: '8', role_name: '普通员工', joined_at: '2026-10-18T10:00:00' }
const role = { id: '8', shop_id: '30', name: '普通员工', created_at: '2026-10-18T10:00:00' }
const application = { id: '9', shop_id: '30', applicant_user_id: '7', status: 'PENDING', reviewer_user_id: null, rejection_reason: null, created_at: '2026-10-18T10:00:00', reviewed_at: null }
const shop = { id: '30', merchant_id: '20', owner_user_id: '1', name: '本店', created_at: '2026-10-18T10:00:00' }
const uuid = '12345678-1234-4234-8234-123456789abc'
function identity(role: IdentityRole = 'SHOP_OWNER') {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.token = 'test-token'; auth.identityState = 'ready'
  auth.user = { id: '1', username: 'test', email: 'test@example.test', role, merchant_id: role === 'NONE' ? null : '20', shop_id: role === 'SHOP_OWNER' ? '30' : null }
  return auth
}
async function open(component: typeof ApplicationsPage | typeof ShopPeoplePage | typeof ShopReadPage | typeof OwnerTransferPage, name: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/view', name, component }, { path: '/', name: 'dashboard', component: { template: '<p>dashboard</p>' } },
    { path: '/shop/warehouse', component: { template: '<p>warehouse</p>' } },
  ] })
  await router.push('/view')
  const wrapper = mount(component, { global: { plugins: [router], stubs: { ElButton } } })
  await flushPromises()
  return { wrapper, router }
}
function button(wrapper: VueWrapper, text: string) { return wrapper.findAll('button').find(b => b.text() === text)! }
async function click(wrapper: VueWrapper, text: string) { await button(wrapper, text).trigger('click'); await flushPromises() }
async function submit(wrapper: VueWrapper) { await wrapper.find('form').trigger('submit'); await flushPromises() }

describe('phase4 identity applications and shop workflows', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    for (const mock of Object.values(api)) mock.mockResolvedValue(page([]))
    api.directory.mockResolvedValue(page([{ id: '20', name: '目标商家' }]))
    api.roles.mockResolvedValue(page([role])); api.employees.mockResolvedValue(page([employee])); api.employeeApplications.mockResolvedValue(page([application]))
    api.applyMerchant.mockResolvedValue({ status: 'PENDING' }); api.applyShop.mockResolvedValue({ status: 'PENDING' }); api.applyEmployee.mockResolvedValue({ status: 'PENDING' }); api.applyWarehouse.mockResolvedValue({ status: 'PENDING' })
    merchant.listShopApplications.mockResolvedValue(page([])); merchant.listShops.mockResolvedValue(page([shop])); merchant.listWarehouseApplications.mockResolvedValue(page([]))
    inventory.getWarehouse.mockResolvedValue({ id: '40', merchant_id: '20', name: '共享仓库' })
    inventory.listStocks.mockResolvedValue(page([{ sku_id: '50', warehouse_id: '40', sku_code: 'SKU', quantity: '10' }]))
    catalog.listCategories.mockResolvedValue(page([])); catalog.listSpus.mockResolvedValue(page([]))
    messageBoxConfirm.mockResolvedValue()
  })
  it('NONE submits merchant application without claiming an identity', async () => {
    const auth = identity('NONE'); const { wrapper } = await open(ApplicationsPage, 'identity-applications')
    await wrapper.find('[aria-label="商家名称"]').setValue('  新商家  '); await submit(wrapper)
    expect(api.applyMerchant).toHaveBeenCalledWith('新商家')
    expect(wrapper.text()).toContain('提交不代表已获得身份'); expect(auth.user?.role).toBe('NONE'); wrapper.unmount()
  })
  it('NONE chooses a real directory merchant and submits a shop application', async () => {
    identity('NONE'); const { wrapper } = await open(ApplicationsPage, 'identity-applications')
    await click(wrapper, '开店申请'); await wrapper.find('[aria-label="目标商家"]').setValue('20'); await wrapper.find('[aria-label="拟建店铺名称"]').setValue('新店'); await submit(wrapper)
    expect(api.applyShop).toHaveBeenCalledWith('20', '新店'); wrapper.unmount()
  })
  it('NONE supplies the owner-provided shop id for employee application', async () => {
    identity('NONE'); const { wrapper } = await open(ApplicationsPage, 'identity-applications')
    await click(wrapper, '员工入职'); await wrapper.find('[aria-label="目标店铺编号"]').setValue('9007199254740997'); await submit(wrapper)
    expect(api.applyEmployee).toHaveBeenCalledWith('9007199254740997'); wrapper.unmount()
  })
  it('refreshes real identity after approval and navigates to the dashboard', async () => {
    const auth = identity('NONE'); const refresh = vi.spyOn(auth, 'refreshIdentity').mockImplementation(async () => { auth.user = { ...auth.user!, role: 'SHOP_OWNER', merchant_id: '20', shop_id: '30' }; return auth.user })
    const { wrapper, router } = await open(ApplicationsPage, 'identity-applications'); await click(wrapper, '刷新申请与身份')
    expect(refresh).toHaveBeenCalledOnce(); expect(router.currentRoute.value.name).toBe('dashboard'); expect(wrapper.find('[aria-label="商家名称"]').element).toHaveProperty('value', ''); wrapper.unmount()
  })
  it('never shows other applicants records', async () => {
    identity('NONE'); api.merchantApplications.mockResolvedValue(page([{ applicant_user_id: '999', merchant_name: 'secret' }]))
    const { wrapper } = await open(ApplicationsPage, 'identity-applications'); expect(wrapper.text()).toContain('其他申请人'); expect(wrapper.text()).not.toContain('secret'); wrapper.unmount()
  })
  it('approves employees once while the write is pending', async () => {
    identity(); let resolve!: () => void; api.reviewEmployee.mockReturnValue(new Promise<void>(r => { resolve = r }))
    const { wrapper } = await open(ShopPeoplePage, 'shop-applications'); await click(wrapper, '批准'); await click(wrapper, '确认审批'); await click(wrapper, '确认审批')
    expect(api.reviewEmployee).toHaveBeenCalledExactlyOnceWith('9', 'APPROVE', undefined); resolve(); await flushPromises(); wrapper.unmount()
  })
  it('validates rejection reason then sends only the trimmed rejection', async () => {
    identity(); const { wrapper } = await open(ShopPeoplePage, 'shop-applications'); await click(wrapper, '驳回'); await click(wrapper, '确认审批')
    expect(api.reviewEmployee).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('驳回原因必填')
    await wrapper.find('[aria-label="驳回原因"]').setValue('  资料不足  '); await click(wrapper, '确认审批'); expect(api.reviewEmployee).toHaveBeenCalledWith('9', 'REJECT', '资料不足'); wrapper.unmount()
  })
  it('hides foreign shop applications and employees and roles', async () => {
    identity(); api.employees.mockResolvedValue(page([{ ...employee, shop_id: '999', role_name: 'foreign-secret' }]))
    const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); expect(wrapper.text()).toContain('其他店铺'); expect(wrapper.text()).not.toContain('foreign-secret'); wrapper.unmount()
    api.employeeApplications.mockResolvedValue(page([{ ...application, shop_id: '999' }]))
    const other = await open(ShopPeoplePage, 'shop-applications'); expect(other.wrapper.text()).toContain('其他店铺'); expect(button(other.wrapper, '批准')).toBeUndefined(); other.wrapper.unmount()
  })
  it('confirms removal and accepts 204 without parsing a JSON result', async () => {
    identity(); api.removeEmployee.mockResolvedValue(undefined); const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); await click(wrapper, '移除员工')
    expect(messageBoxConfirm).toHaveBeenCalled(); expect(api.removeEmployee).toHaveBeenCalledWith('30', '7'); expect(wrapper.text()).toContain('204'); expect(api.employees).toHaveBeenCalledTimes(2); wrapper.unmount()
  })
  it('does not remove an employee when the confirmation is cancelled', async () => {
    identity(); messageBoxConfirm.mockRejectedValueOnce('cancel'); const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); await click(wrapper, '移除员工'); expect(api.removeEmployee).not.toHaveBeenCalled(); wrapper.unmount()
  })
  it('creates a role name without configuring permissions', async () => {
    identity(); const { wrapper } = await open(ShopPeoplePage, 'shop-roles'); await wrapper.find('input').setValue('销售专员'); await submit(wrapper)
    expect(api.createRole).toHaveBeenCalledWith('30', '销售专员'); expect(wrapper.text()).toContain('不会改变实际权限'); wrapper.unmount()
  })
  it('assigns only a local returned role and reloads the employee relationship', async () => {
    identity(); const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); await wrapper.find('select').setValue('8'); await click(wrapper, '保存岗位')
    expect(api.assignRole).toHaveBeenCalledWith('30', '7', '8'); expect(api.employees).toHaveBeenCalledTimes(2); wrapper.unmount()
  })
  it('rejects foreign role options instead of offering assignment', async () => {
    identity(); api.roles.mockResolvedValue(page([{ ...role, shop_id: '999', name: 'foreign-role' }]))
    const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); expect(wrapper.text()).not.toContain('foreign-role'); expect(api.assignRole).not.toHaveBeenCalled(); wrapper.unmount()
  })
  it('keeps unapproved warehouse 403 on the resource page and clears old inventory', async () => {
    const auth = identity(); const { wrapper, router } = await open(ShopReadPage, 'shop-inventory'); expect(wrapper.text()).toContain('共享仓库')
    inventory.getWarehouse.mockRejectedValue(new ApiError('未授权', 403)); await click(wrapper, '刷新')
    expect(wrapper.text()).toContain('前往仓库授权申请'); expect(wrapper.text()).not.toContain('共享仓库 40'); expect(wrapper.find('table').exists()).toBe(false)
    expect(auth.user?.role).toBe('SHOP_OWNER'); expect(router.currentRoute.value.name).toBe('shop-inventory'); wrapper.unmount()
  })
  it('submits warehouse access with no client tenant arguments and reloads state', async () => {
    identity(); const { wrapper } = await open(ShopReadPage, 'shop-warehouse'); await click(wrapper, '提交仓库授权申请')
    expect(api.applyWarehouse).toHaveBeenCalledWith(); expect(merchant.listWarehouseApplications).toHaveBeenCalledTimes(2); expect(wrapper.text()).toContain('批准前不会获得库存访问权'); wrapper.unmount()
  })
  it('renders authorized stock read-only without inventory operations or product editing', async () => {
    identity(); const { wrapper } = await open(ShopReadPage, 'shop-inventory'); expect(wrapper.text()).toContain('SKU'); expect(wrapper.text()).toContain('10'); expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.findAll('button').some(b => /提交调整|创建SKU|上架/.test(b.text()))).toBe(false); wrapper.unmount()
  })
  it('drops stale employee responses after the shop scope changes', async () => {
    const auth = identity(); let resolve!: (value: unknown) => void; api.employees.mockReturnValueOnce(new Promise(r => { resolve = r }))
    const { wrapper } = await open(ShopPeoplePage, 'shop-employees'); api.employees.mockResolvedValue(page([])); api.roles.mockResolvedValue(page([]))
    auth.user = { ...auth.user!, shop_id: '31' }; await flushPromises(); resolve(page([{ ...employee, role_name: 'old-secret' }])); await flushPromises()
    expect(wrapper.text()).not.toContain('old-secret'); expect(wrapper.findAll('tbody tr')).toHaveLength(0); wrapper.unmount()
  })
  it('displays proposed UUID without claiming a completed handoff and clears it on identity change', async () => {
    const auth = identity(); api.transferOwner.mockResolvedValue({ shop_id: '30', owner_user_id: '1', status: 'PROPOSED', proposal_id: uuid, target_user_id: '7' })
    const { wrapper } = await open(OwnerTransferPage, 'shop-owner-transfer'); await wrapper.find('input').setValue('7'); await submit(wrapper)
    expect(api.transferOwner).toHaveBeenCalledWith('30', { action: 'PROPOSE', target_user_id: '7' }); expect(wrapper.text()).toContain(uuid); expect(wrapper.text()).toContain('尚未完成交接')
    auth.user = { ...auth.user!, role: 'NONE', merchant_id: null, shop_id: null }; await flushPromises(); expect(wrapper.text()).not.toContain(uuid); wrapper.unmount()
  })
  it('confirms the exact UUID then revalidates identity and reads current shops', async () => {
    const auth = identity('MERCHANT_ADMIN'), current = { ...auth.user! }; const refresh = vi.spyOn(auth, 'refreshIdentity').mockImplementation(async () => { auth.scopeEpoch++; auth.user = null; await Promise.resolve(); auth.user = current; return current })
    api.transferOwner.mockResolvedValue({ shop_id: '30', owner_user_id: '7', status: 'CONFIRMED', proposal_id: null, target_user_id: '7' })
    const { wrapper } = await open(OwnerTransferPage, 'merchant-owner-transfer'); await wrapper.find('select').setValue('30'); await wrapper.find('input').setValue(uuid); await submit(wrapper)
    expect(api.transferOwner).toHaveBeenCalledWith('30', { action: 'CONFIRM', proposal_id: uuid }); expect(refresh).toHaveBeenCalledOnce(); expect(wrapper.text()).toContain('CONFIRMED'); expect(elementMessages.success).toHaveBeenCalled(); wrapper.unmount()
  })
  it.each([409, 403, 404, 422])('shows handoff HTTP %s failure without a false successful result', async status => {
    identity('MERCHANT_ADMIN'); api.transferOwner.mockRejectedValue(new ApiError('请求被拒绝', status, status === 422 ? { proposal_id: ['UUID不合法'] } : {}))
    const { wrapper } = await open(OwnerTransferPage, 'merchant-owner-transfer'); await wrapper.find('select').setValue('30'); await wrapper.find('input').setValue(uuid); await submit(wrapper)
    expect(wrapper.find('.workspace-error').exists()).toBe(true); expect(wrapper.find('.workspace-uuid').exists()).toBe(false); expect(wrapper.text()).not.toContain('服务端交接结果'); wrapper.unmount()
  })
  it('requires a nonblank FORCE reason and second confirmation before writing', async () => {
    identity('MERCHANT_ADMIN'); const { wrapper } = await open(OwnerTransferPage, 'merchant-owner-transfer')
    await wrapper.find('[aria-label="目标店铺"]').setValue('30'); await wrapper.find('[aria-label="操作类型"]').setValue('FORCE'); await wrapper.find('input').setValue('7'); await submit(wrapper)
    expect(api.transferOwner).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('原因必填')
    await wrapper.find('textarea').setValue('  账号遗失  '); messageBoxConfirm.mockRejectedValueOnce('cancel'); await submit(wrapper)
    expect(api.transferOwner).not.toHaveBeenCalled(); expect(messageBoxConfirm).toHaveBeenCalledWith(expect.stringContaining('不代表旧负责人同意'), '异常强制交接', expect.any(Object))
    api.transferOwner.mockRejectedValueOnce(new ApiError('conflict', 409)); await submit(wrapper)
    expect(api.transferOwner).toHaveBeenCalledWith('30', { action: 'FORCE', target_user_id: '7', reason: '账号遗失' }); wrapper.unmount()
  })
  it('rejects a foreign merchants shop before offering handoff', async () => {
    identity('MERCHANT_ADMIN'); merchant.listShops.mockResolvedValue(page([{ ...shop, merchant_id: '99', name: 'foreign-shop' }]))
    const { wrapper } = await open(OwnerTransferPage, 'merchant-owner-transfer'); expect(wrapper.text()).toContain('其他商家'); expect(wrapper.text()).not.toContain('foreign-shop'); expect(wrapper.find('form').exists()).toBe(false); wrapper.unmount()
  })
})
