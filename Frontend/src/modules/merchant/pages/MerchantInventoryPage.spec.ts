import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'
import { ApiError } from '@/shared/http/errors'
import { ElButton, ElInput } from '@/modules/merchant/test-support/elementPlus'
import MerchantInventoryPage from './MerchantInventoryPage.vue'

const api = vi.hoisted(() => ({ getWarehouse: vi.fn(), listStocks: vi.fn(), listMovements: vi.fn(), adjustStock: vi.fn() }))
vi.mock('@/modules/merchant/inventoryApi', () => api)
vi.mock('element-plus', async () => (await import('@/modules/merchant/test-support/elementPlus')).elementPlusMock)

const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }, { path: '/forbidden', name: 'forbidden', component: { template: '<div />' } }] })
const key = '12345678-1234-4234-8234-123456789abc'
const skuId = '9007199254740997'
const warehouse = { id: '9007199254740995', merchant_id: '10', name: '主仓', created_at: '2026-10-01T10:00:00' }
const stock = (quantity = '10', warehouseId = warehouse.id) => ({ sku_id: skuId, sku_code: 'SKU-1', specification: '规格', spu_id: '9007199254740999', spu_name: '茶', warehouse_id: warehouseId, quantity })
const movement = (values: Record<string, unknown> = {}) => ({
  movement_id: '31', sku_id: skuId, quantity_before: '10', quantity_delta: '1', quantity_after: '11', idempotency_key: null,
  merchant_id: '10', warehouse_id: warehouse.id, actor_user_id: '5', operation_type: 'MANUAL_ADJUSTMENT', order_item_id: null, created_at: '2026-10-01T10:00:00', ...values,
})
const page = <T>(items: T[], limit = 20, offset = 0, total = items.length) => ({ items, limit, offset, total })
const operationResult = (idempotency_key: string, delta = '1') => ({ movement_id: '31', sku_id: skuId, quantity_before: '10', quantity_delta: delta, quantity_after: String(10 + Number(delta)), idempotency_key })

async function setMerchant(userId = '5', merchantId = '10') {
  setActivePinia(createPinia())
  const { useAuthStore } = await import('@/modules/auth/store')
  const auth = useAuthStore()
  auth.token = 'merchant-token'; auth.identityState = 'ready'
  auth.user = { id: userId, username: 'merchant', email: 'merchant@example.test', role: 'MERCHANT_ADMIN', merchant_id: merchantId, shop_id: null }
  return auth
}

function setReadResponses(options: { warehouse?: typeof warehouse; stocks?: ReturnType<typeof page<ReturnType<typeof stock>>>; movements?: ReturnType<typeof page<ReturnType<typeof movement>>> } = {}) {
  api.getWarehouse.mockResolvedValue(options.warehouse ?? warehouse)
  api.listStocks.mockResolvedValue(options.stocks ?? page([stock()]))
  api.listMovements.mockResolvedValue(options.movements ?? page([movement()]))
  api.adjustStock.mockResolvedValue(operationResult(key))
}

async function openForm(wrapper: ReturnType<typeof mount>, delta: string) {
  await wrapper.find('select').setValue(skuId)
  await wrapper.find('input').setValue(delta)
  await wrapper.findAll('button').find((button) => button.text() === '提交调整')!.trigger('click')
  await flushPromises()
}

describe('merchant inventory page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue(key) })
    setReadResponses()
  })

  it('validates zero, unsafe, and below-zero deltas before sending; accepts signed safe deltas', async () => {
    await setMerchant()
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await openForm(wrapper, '0')
    expect(api.adjustStock).not.toHaveBeenCalled()
    await openForm(wrapper, '9007199254740992')
    expect(api.adjustStock).not.toHaveBeenCalled()
    await openForm(wrapper, '-11')
    expect(api.adjustStock).not.toHaveBeenCalled()
    await openForm(wrapper, '-3')
    expect(api.adjustStock).toHaveBeenCalledWith(skuId, -3, key)
    wrapper.unmount()
  })

  it('keeps a network-unknown operation stored and locked until an explicit retry reuses its key', async () => {
    await setMerchant()
    api.adjustStock.mockRejectedValueOnce(new ApiError('network unavailable', undefined, {}, 'network')).mockResolvedValueOnce(operationResult(key))
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await openForm(wrapper, '1')
    expect(api.adjustStock).toHaveBeenCalledTimes(1)
    expect(api.adjustStock).toHaveBeenCalledWith(skuId, 1, key)
    expect(wrapper.text()).toContain('上一笔库存操作结果未知')
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toContain(key)
    expect(wrapper.findAll('button').find((button) => button.text() === '提交调整')!.attributes('disabled')).toBeDefined()

    await wrapper.findAll('button').find((button) => button.text() === '刷新数据')!.trigger('click')
    await flushPromises()
    expect(api.adjustStock).toHaveBeenCalledTimes(1)
    await wrapper.findAll('button').find((button) => button.text() === '我已确认，使用同一幂等键重试')!.trigger('click')
    await flushPromises()
    expect(api.adjustStock).toHaveBeenCalledTimes(2)
    expect(api.adjustStock.mock.calls[1]).toEqual([skuId, 1, key])
    expect(wrapper.text()).toContain('操作已确认')
    wrapper.unmount()
  })

  it('keeps 503 responses unknown and does not create a new key or resend automatically', async () => {
    await setMerchant()
    api.adjustStock.mockRejectedValueOnce(new ApiError('unavailable', 503, {}, 'unavailable')).mockResolvedValueOnce(operationResult(key))
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await openForm(wrapper, '2')
    expect(api.adjustStock).toHaveBeenCalledTimes(1)
    expect(api.adjustStock).toHaveBeenCalledWith(skuId, 2, key)
    expect(wrapper.text()).toContain('服务返回 503，请求结果未知')
    await flushPromises()
    expect(api.adjustStock).toHaveBeenCalledTimes(1)
    await wrapper.findAll('button').find((button) => button.text() === '我已确认，使用同一幂等键重试')!.trigger('click')
    await flushPromises()
    expect(api.adjustStock.mock.calls[1]).toEqual([skuId, 2, key])
    wrapper.unmount()
  })

  it('clears a definitive insufficient-stock conflict instead of treating it as an unknown result', async () => {
    await setMerchant()
    api.adjustStock.mockRejectedValueOnce(new ApiError('冲突', 409, {}, 'conflict'))
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await openForm(wrapper, '-2')
    expect(api.adjustStock).toHaveBeenCalledWith(skuId, -2, key)
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toBeNull()
    expect(wrapper.text()).not.toContain('上一笔库存操作结果未知')
    expect(wrapper.text()).toContain('操作未成功')
    wrapper.unmount()
  })

  it('clears the captured pending key before a real 403 navigation unmounts the page', async () => {
    await setMerchant()
    api.adjustStock.mockRejectedValueOnce(new ApiError('forbidden', 403, {}, 'forbidden'))
    const navigationRouter = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/inventory', component: MerchantInventoryPage },
        { path: '/forbidden', name: 'forbidden', component: { template: '<main>Forbidden destination</main>' } },
      ],
    })
    await navigationRouter.push('/inventory')
    await navigationRouter.isReady()
    const host = mount(defineComponent({ template: '<RouterView />' }), { global: { plugins: [navigationRouter], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await openForm(host, '1')
    await flushPromises()
    expect(navigationRouter.currentRoute.value.name).toBe('forbidden')
    expect(host.text()).toContain('Forbidden destination')
    expect(host.findComponent(MerchantInventoryPage).exists()).toBe(false)
    expect(api.adjustStock).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toBeNull()
    host.unmount()
  })

  it('restores unknown operations only for the same user and merchant across refreshes', async () => {
    await setMerchant('5', '10')
    sessionStorage.setItem('seller_inventory_pending:5:10', JSON.stringify({ key, sku_id: skuId, delta: 4, state: 'unknown', createdAt: '2026-10-18T00:00:00.000Z' }))
    let wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(wrapper.text()).toContain(key)
    expect(api.adjustStock).not.toHaveBeenCalled()
    wrapper.unmount()

    wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(wrapper.text()).toContain(key)
    expect(api.adjustStock).not.toHaveBeenCalled()

    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: '6', merchant_id: '20' }
    await flushPromises()
    expect(wrapper.text()).not.toContain(key)
    expect(wrapper.text()).not.toContain('上一笔库存操作结果未知')
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toContain(key)
    auth.user = { ...auth.user!, id: '5', merchant_id: '10' }
    await flushPromises()
    expect(wrapper.text()).toContain(key)
    expect(api.adjustStock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not confirm a movement when the same key has different request parameters', async () => {
    await setMerchant()
    sessionStorage.setItem('seller_inventory_pending:5:10', JSON.stringify({ key, sku_id: skuId, delta: 1, state: 'unknown', createdAt: '2026-10-18T00:00:00.000Z' }))
    api.listMovements.mockImplementation(async (limit, offset, requestedSku) => requestedSku
      ? page([movement({ idempotency_key: key, sku_id: requestedSku, quantity_delta: '2', actor_user_id: '5', merchant_id: '10' })], limit, offset, 1)
      : page([movement()], limit, offset))
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '核对库存和流水')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('相同幂等键但请求参数或身份不匹配')
    expect(wrapper.text()).toContain('上一笔库存操作结果未知')
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toContain('"state":"unknown"')
    expect(api.adjustStock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps verified warehouse, stock, and movement views unchanged when verification rows cross tenant or warehouse scope', async () => {
    await setMerchant()
    sessionStorage.setItem('seller_inventory_pending:5:10', JSON.stringify({ key, sku_id: skuId, delta: 1, state: 'unknown', createdAt: '2026-10-18T00:00:00.000Z' }))
    let verifyCount = 0
    api.getWarehouse.mockImplementation(async () => verifyCount === 0 ? warehouse : { ...warehouse, id: '88', merchant_id: '99', name: '越权仓库' })
    api.listStocks.mockImplementation(async (limit) => limit === 100
      ? page([{ ...stock('500', '88'), sku_code: '越权库存' }])
      : page([stock()]))
    api.listMovements.mockImplementation(async (limit, offset, requestedSku) => {
      if (limit !== 100) return page([movement()], limit, offset)
      verifyCount += 1
      return page([movement({ idempotency_key: key, sku_id: requestedSku, merchant_id: '99', warehouse_id: '88' })], limit, offset, 1)
    })
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text() === '核对库存和流水')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('服务器返回了其他商家的库存核对数据。')
    expect(wrapper.text()).toContain('主仓')
    expect(wrapper.text()).toContain('SKU-1')
    expect(wrapper.text()).not.toContain('越权仓库')
    expect(wrapper.text()).not.toContain('越权库存')
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toContain('"state":"unknown"')

    api.getWarehouse.mockResolvedValue({ ...warehouse, id: '88', name: '错误仓库编号' })
    api.listMovements.mockImplementation(async (limit, offset, requestedSku) => limit === 100
      ? page([movement({ idempotency_key: key, sku_id: requestedSku, warehouse_id: '99' })], limit, offset, 1)
      : page([movement()], limit, offset))
    await wrapper.findAll('button').find((button) => button.text() === '核对库存和流水')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('服务器返回了其他商家的库存核对数据。')
    expect(wrapper.text()).toContain('主仓')
    expect(wrapper.text()).toContain('SKU-1')
    expect(wrapper.text()).not.toContain('错误仓库编号')
    expect(sessionStorage.getItem('seller_inventory_pending:5:10')).toContain('"state":"unknown"')
    wrapper.unmount()
  })

  it('uses verification-only pages for comparison, then reloads the current main-list pages without SKU filtering', async () => {
    await setMerchant()
    sessionStorage.setItem('seller_inventory_pending:5:10', JSON.stringify({ key, sku_id: skuId, delta: 1, state: 'unknown', createdAt: '2026-10-18T00:00:00.000Z' }))
    api.listStocks.mockImplementation(async (limit, offset) => limit === 100
      ? page([{ ...stock(), sku_code: 'VERIFY-ONLY-STOCK' }], limit, offset, 1)
      : page([{ ...stock(), sku_code: offset === 20 ? 'CURRENT-PAGE-STOCK' : 'FIRST-PAGE-STOCK' }], limit, offset, 41))
    api.listMovements.mockImplementation(async (limit, offset) => limit === 100
      ? page([movement({ movement_id: 'VERIFY-ONLY-MOVEMENT', idempotency_key: null })], limit, offset, 1)
      : page([movement({ movement_id: offset === 20 ? 'CURRENT-PAGE-MOVEMENT' : 'FIRST-PAGE-MOVEMENT' })], limit, offset, 41))
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()

    const paginations = wrapper.findAll('.test-pagination')
    expect(paginations).toHaveLength(2)
    await paginations[0].findAll('button').find((button) => button.text() === '下一页')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.test-pagination')[1].findAll('button').find((button) => button.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(api.listStocks).toHaveBeenLastCalledWith(20, 20)
    expect(api.listMovements).toHaveBeenLastCalledWith(20, 20)

    await wrapper.findAll('button').find((button) => button.text() === '核对库存和流水')!.trigger('click')
    await flushPromises()
    expect(api.listStocks).toHaveBeenCalledWith(100, 0)
    expect(api.listMovements).toHaveBeenCalledWith(100, 0, skuId)
    expect(api.listStocks).toHaveBeenLastCalledWith(20, 20)
    expect(api.listMovements).toHaveBeenLastCalledWith(20, 20)
    expect(wrapper.text()).toContain('CURRENT-PAGE-STOCK')
    expect(wrapper.text()).toContain('CURRENT-PAGE-MOVEMENT')
    expect(wrapper.text()).not.toContain('VERIFY-ONLY-STOCK')
    expect(wrapper.text()).not.toContain('VERIFY-ONLY-MOVEMENT')
    wrapper.unmount()
  })

  it('hides stock data from a different warehouse merchant', async () => {
    await setMerchant()
    setReadResponses({ warehouse: { ...warehouse, merchant_id: '99' }, stocks: page([stock()]) })
    const wrapper = mount(MerchantInventoryPage, { global: { plugins: [router], stubs: { ElButton, ElInput } } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('SKU-1')
    expect(wrapper.text()).toContain('服务器返回了其他商家的仓库或库存数据。')
    wrapper.unmount()
  })
})
