import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/shared/http/errors'
import MerchantCatalogPage from './MerchantCatalogPage.vue'
import { ElButton, ElInput, messageBoxConfirm } from '@/modules/merchant/test-support/elementPlus'

const api = vi.hoisted(() => ({
  listCategories: vi.fn(), createCategory: vi.fn(), listSpus: vi.fn(), createSpu: vi.fn(), getSpu: vi.fn(), updateSpu: vi.fn(),
  createSku: vi.fn(), updateSku: vi.fn(), publishSpu: vi.fn(), submitOffshelfApplication: vi.fn(), listOffshelfApplications: vi.fn(),
}))
vi.mock('@/modules/merchant/catalogApi', () => api)
vi.mock('element-plus', async () => (await import('@/modules/merchant/test-support/elementPlus')).elementPlusMock)

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/', component: { template: '<div />' } }, { path: '/forbidden', name: 'forbidden', component: { template: '<div />' } }],
})
const category = (id: string, merchantId = '10', name = `分类 ${id}`) => ({ id, merchant_id: merchantId, name, created_at: '2026-10-01T10:00:00' })
const spu = (id: string, merchantId = '10', status: 'OFF_SALE' | 'ON_SALE' = 'OFF_SALE') => ({ id, merchant_id: merchantId, category_id: '4', name: `商品 ${id}`, description: null, status, created_at: '2026-10-01T10:00:00', updated_at: '2026-10-01T10:00:00' })
const application = (id: string, status: 'PENDING' | 'APPROVED' | 'REJECTED') => ({
  id, spu_id: '8', applicant_user_id: '20', status, reviewer_user_id: status === 'PENDING' ? null : '30',
  rejection_reason: status === 'REJECTED' ? '资料不完整' : null, created_at: '2026-10-01T10:00:00', reviewed_at: null,
  spu: { id: '8', name: `历史商品 ${id}`, status: 'ON_SALE', merchant_id: '10' },
})
const page = <T>(items: T[], limit = 20, offset = 0, total = items.length) => ({ items, limit, offset, total })

async function setMerchant(merchantId = '10', userId = '5') {
  setActivePinia(createPinia())
  const { useAuthStore } = await import('@/modules/auth/store')
  const auth = useAuthStore()
  auth.token = 'merchant-token'; auth.identityState = 'ready'
  auth.user = { id: userId, username: 'merchant', email: 'merchant@example.test', role: 'MERCHANT_ADMIN', merchant_id: merchantId, shop_id: null }
  return auth
}

function defaultResponses() {
  api.listCategories.mockResolvedValue(page([category('4')], 100, 0, 1))
  api.listSpus.mockResolvedValue(page([spu('8')]))
  api.listOffshelfApplications.mockResolvedValue(page([] , 10))
  api.createCategory.mockResolvedValue(category('5'))
  api.createSpu.mockResolvedValue(spu('9'))
  api.getSpu.mockResolvedValue({ ...spu('8'), skus: [] })
  api.createSku.mockResolvedValue({ id: '22', spu_id: '8', sku_code: 'SKU', specification: '规格', price: '1.00' })
  api.updateSpu.mockResolvedValue(spu('8'))
  api.updateSku.mockResolvedValue({ id: '22', spu_id: '8', sku_code: 'SKU', specification: '规格', price: '1.00' })
  api.publishSpu.mockResolvedValue(spu('8', '10', 'ON_SALE'))
  api.submitOffshelfApplication.mockResolvedValue(application('1', 'PENDING'))
}

describe('merchant catalog page', () => {
  beforeEach(() => { vi.clearAllMocks(); defaultResponses(); messageBoxConfirm.mockResolvedValue(undefined) })

  it('creates a trimmed category and requests category pages in batches of 100', async () => {
    await setMerchant()
    api.listCategories.mockImplementation(async (_limit, offset) => page(offset === 0 ? [category('4')] : [category('104')], 100, offset, 101))
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(wrapper.text()).toContain('分类 4')
    expect(wrapper.findAll('.test-pagination')).toHaveLength(1)
    await wrapper.findAll('button').find((button) => button.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(api.listCategories).toHaveBeenLastCalledWith(100, 100)
    expect(wrapper.text()).toContain('分类 104')

    await wrapper.findAll('button').find((button) => button.text() === '新建分类')!.trigger('click')
    await wrapper.find('.test-dialog input').setValue('  茶饮  ')
    await wrapper.findAll('.test-dialog button').find((button) => button.text() === '创建')!.trigger('click')
    await flushPromises()
    expect(api.createCategory).toHaveBeenCalledWith('茶饮')
  })

  it('keeps new SPUs off sale, validates the SKU gate, and publishes only after a SKU exists', async () => {
    await setMerchant()
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建商品')!.trigger('click')
    const spuDialog = wrapper.find('[aria-label="新建商品"]')
    await spuDialog.find('input').setValue('新商品')
    await spuDialog.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(api.createSpu).toHaveBeenCalledWith({ category_id: '4', name: '新商品', description: null })
    expect(wrapper.text()).toContain('未上架')

    await wrapper.findAll('button').find((button) => button.text() === '详情')!.trigger('click')
    await flushPromises()
    const publishButton = wrapper.findAll('button').find((button) => button.text() === '上架')!
    expect(publishButton.attributes('disabled')).toBeDefined()
    await publishButton.trigger('click')
    expect(api.publishSpu).not.toHaveBeenCalled()

    api.getSpu.mockResolvedValueOnce({ ...spu('8'), skus: [{ id: '22', spu_id: '8', sku_code: 'SKU', specification: '规格', price: '0.10' }] })
    await wrapper.findAll('button').find((button) => button.text() === '详情')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '上架')!.trigger('click')
    await flushPromises()
    expect(api.publishSpu).toHaveBeenCalledWith('8')
    wrapper.unmount()
  })

  it('keeps SKU prices as exact decimal text and displays offshelf history statuses', async () => {
    await setMerchant()
    api.listOffshelfApplications.mockResolvedValue(page([application('1', 'PENDING'), application('2', 'APPROVED'), application('3', 'REJECTED')], 10, 0, 3))
    api.getSpu.mockResolvedValue({ ...spu('8'), skus: [] })
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(wrapper.text()).toContain('待审核')
    expect(wrapper.text()).toContain('已批准')
    expect(wrapper.text()).toContain('已驳回')

    await wrapper.findAll('button').find((button) => button.text() === '详情')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建 SKU')!.trigger('click')
    const skuDialog = wrapper.find('[aria-label="新建 SKU"]')
    const inputs = skuDialog.findAll('input')
    await inputs[0].setValue('SKU-EXACT')
    await inputs[1].setValue('大包装')
    await inputs[2].setValue('9999999999.99')
    await skuDialog.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(api.createSku).toHaveBeenCalledWith('8', { sku_code: 'SKU-EXACT', specification: '大包装', price: '9999999999.99' })
    wrapper.unmount()
  })

  it('keeps a 404 detail error visible and retries the same SPU until its detail loads', async () => {
    await setMerchant()
    api.getSpu.mockRejectedValueOnce(new ApiError('not found', 404, {}, 'not_found')).mockResolvedValueOnce({
      ...spu('8'), name: '真实商品详情', description: '详情已恢复',
      skus: [{ id: '22', spu_id: '8', sku_code: 'REAL-SKU', specification: '单件', price: '12.30' }],
    })
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '详情')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('[aria-label="商品详情"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('内容不存在或已不可见。')
    expect(api.getSpu).toHaveBeenCalledTimes(1)
    expect(api.getSpu).toHaveBeenLastCalledWith('8')

    await dialog.findAll('button').find((button) => button.text() === '重试')!.trigger('click')
    await flushPromises()
    expect(api.getSpu).toHaveBeenCalledTimes(2)
    expect(api.getSpu).toHaveBeenLastCalledWith('8')
    expect(wrapper.find('[aria-label="真实商品详情"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('详情已恢复')
    expect(wrapper.text()).toContain('REAL-SKU')
    wrapper.unmount()
  })

  it('rejects cross-merchant rows and discards a response from the previous merchant scope', async () => {
    await setMerchant('10')
    api.listCategories.mockResolvedValue(page([category('4', '99', '不应展示的分类')], 100))
    const badWrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(badWrapper.text()).not.toContain('不应展示的分类')
    expect(badWrapper.text()).toContain('服务器返回了其他商家的商品数据。')
    badWrapper.unmount()

    setActivePinia(createPinia())
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth2 = useAuthStore()
    auth2.token = 'merchant-token'; auth2.identityState = 'ready'
    auth2.user = { id: '5', username: 'merchant', email: 'merchant@example.test', role: 'MERCHANT_ADMIN', merchant_id: '10', shop_id: null }
    let resolveOldCategories!: (result: ReturnType<typeof page<ReturnType<typeof category>>>) => void
    let categoryCalls = 0
    api.listCategories.mockImplementation(() => categoryCalls++ === 0
      ? new Promise((resolve) => { resolveOldCategories = resolve })
      : Promise.resolve(page([category('40', '20', '新商家分类')], 100)))
    let spuCalls = 0
    api.listSpus.mockImplementation(async () => page([spu('8', spuCalls++ === 0 ? '10' : '20')]))
    api.listOffshelfApplications.mockImplementation(async () => page([], 10))
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    auth2.user = { ...auth2.user!, id: '6', merchant_id: '20' }
    await flushPromises()
    resolveOldCategories(page([category('4', '10', '旧商家数据')], 100))
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧商家数据')
    expect(wrapper.text()).toContain('新商家分类')
    wrapper.unmount()
  })

  it('does not route or toast a stale 403 from merchant A after switching to merchant B', async () => {
    const auth = await setMerchant('10')
    let rejectOld!: (cause: unknown) => void
    let categoryCalls = 0
    api.listCategories.mockImplementation(() => categoryCalls++ === 0
      ? new Promise((_resolve, reject) => { rejectOld = reject })
      : Promise.resolve(page([category('40', '20', '商家乙分类')], 100)))
    let spuCalls = 0
    api.listSpus.mockImplementation(async () => page([spu('8', spuCalls++ === 0 ? '10' : '20')]))
    api.listOffshelfApplications.mockImplementation(async () => page([], 10))
    const replace = vi.spyOn(router, 'replace')
    const wrapper = mount(MerchantCatalogPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    auth.user = { ...auth.user!, id: '6', merchant_id: '20' }
    await flushPromises()
    expect(wrapper.text()).toContain('商家乙分类')
    rejectOld(new ApiError('current account is forbidden', 403, {}, 'forbidden'))
    await flushPromises()
    expect(replace).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('当前账号没有访问权限。')
    wrapper.unmount()
    replace.mockRestore()
  })
})
