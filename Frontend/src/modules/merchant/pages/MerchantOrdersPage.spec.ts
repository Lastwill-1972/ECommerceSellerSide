import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MerchantOrdersPage from './MerchantOrdersPage.vue'
import ButtonStub from '@/modules/merchant/test-support/ButtonStub'
import SelectStub from '@/modules/merchant/test-support/SelectStub'
import OptionStub from '@/modules/merchant/test-support/OptionStub'
import { listOrders, listShops } from '@/modules/merchant/api'

vi.mock('@/modules/merchant/api', () => ({ listOrders: vi.fn(), listShops: vi.fn() }))
const order = { id: '9007199254740993', merchant_id: '9007199254740995', shop_id: '9007199254740997', created_by_user_id: '9007199254740999', status: 'PENDING', total_amount: '12345678901234567890.10', created_at: '2026-10-17T09:30:00', completed_at: null, cancelled_at: null, items: [] }
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }, { path: '/merchant/orders', component: { template: '<div />' } }, { path: '/merchant/orders/:id', component: { template: '<div />' }, name: 'merchant-order-detail' }] })

describe('merchant order list', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const page = { items: [order], limit: 20, offset: 0, total: 1 }
    vi.mocked(listOrders).mockResolvedValue(page as never)
    vi.mocked(listShops).mockResolvedValue({ items: [{ id: order.shop_id, merchant_id: order.merchant_id, name: '订单店铺', owner_user_id: '6', created_at: order.created_at }], limit: 100, offset: 0, total: 1 } as never)
  })

  it('renders read-only order snapshot data and supports server filters', async () => {
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'token'; auth.identityState = 'ready'
    auth.user = { id: '5', username: 'admin', email: 'm@example.test', role: 'MERCHANT_ADMIN', merchant_id: order.merchant_id, shop_id: null }
    const wrapper = mount(MerchantOrdersPage, { global: { plugins: [router], stubs: { ElButton: ButtonStub, ElSelect: SelectStub, ElOption: OptionStub } } })
    await flushPromises()
    expect(wrapper.text()).toContain('12,345,678,901,234,567,890.10')
    expect(wrapper.text()).toContain('订单店铺')
    expect(wrapper.findAll('a').some((link) => link.text() === order.id)).toBe(true)
    expect(wrapper.findAll('button').map((node) => node.text()).join(' ')).not.toMatch(/完成|取消订单|删除/)
    const selects = wrapper.findAll('select')
    await selects[0].setValue('CANCELLED')
    await flushPromises()
    await selects[1].setValue(order.shop_id)
    await flushPromises()
    expect(listOrders).toHaveBeenLastCalledWith(20, 0, { status: 'CANCELLED', shop_id: order.shop_id })
    expect(listOrders).toHaveBeenCalledWith(20, 0, { status: 'CANCELLED', shop_id: undefined })
    wrapper.unmount()
  })

  it('explains when the shop filter only includes the first 100 shops', async () => {
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'token'; auth.identityState = 'ready'
    auth.user = { id: '5', username: 'admin', email: 'm@example.test', role: 'MERCHANT_ADMIN', merchant_id: order.merchant_id, shop_id: null }
    vi.mocked(listShops).mockResolvedValue({ items: Array.from({ length: 100 }, (_, index) => ({ id: String(index + 1), merchant_id: order.merchant_id, name: `店铺${index + 1}`, owner_user_id: '6', created_at: order.created_at })), limit: 100, offset: 0, total: 101 } as never)
    const wrapper = mount(MerchantOrdersPage, { global: { plugins: [router], stubs: { ElButton: ButtonStub, ElSelect: SelectStub, ElOption: OptionStub } } })
    await flushPromises()
    expect(wrapper.text()).toContain('当前商家共有 101 家店铺，筛选器显示前 100 家；未列出的店铺可通过“全部店铺”查看订单。')
    wrapper.unmount()
  })

  it('clears a prior merchant shop filter and discards its late order response', async () => {
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'token'; auth.identityState = 'ready'
    auth.user = { id: '5', username: 'admin', email: 'm@example.test', role: 'MERCHANT_ADMIN', merchant_id: order.merchant_id, shop_id: null }
    let resolveOldOrders!: (value: { items: typeof order[]; limit: number; offset: number; total: number }) => void
    vi.mocked(listOrders).mockImplementation((_limit, _offset, filters) => filters.shop_id
      ? new Promise((resolve) => { resolveOldOrders = resolve }) as never
      : Promise.resolve({ items: [], limit: 20, offset: 0, total: 0 }) as never)
    const wrapper = mount(MerchantOrdersPage, { global: { plugins: [router], stubs: { ElButton: ButtonStub, ElSelect: SelectStub, ElOption: OptionStub } } })
    await flushPromises()
    const shopSelect = wrapper.findAll('select')[1]
    await shopSelect.setValue(order.shop_id)
    expect(listOrders).toHaveBeenLastCalledWith(20, 0, { status: undefined, shop_id: order.shop_id })

    auth.user = { ...auth.user!, merchant_id: '9007199254741999' }
    await flushPromises()
    expect((wrapper.findAll('select')[1].element as HTMLSelectElement).value).toBe('')
    resolveOldOrders({ items: [{ ...order, id: '9007199254745999' }], limit: 20, offset: 0, total: 1 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('9007199254745999')
    wrapper.unmount()
  })
})
