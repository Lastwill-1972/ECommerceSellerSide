import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MerchantOrdersPage from './MerchantOrdersPage.vue'
import { listOrders, listShops } from '@/modules/merchant/api'

vi.mock('@/modules/merchant/api', () => ({ listOrders: vi.fn(), listShops: vi.fn() }))
const order = { id: '9007199254740993', merchant_id: '9007199254740995', shop_id: '9007199254740997', created_by_user_id: '9007199254740999', status: 'PENDING', total_amount: '12345678901234567890.10', created_at: '2026-10-17T09:30:00', completed_at: null, cancelled_at: null, items: [] }
const button = defineComponent({ props: ['disabled', 'loading'], emits: ['click'], setup(props, { slots, emit }) { return () => h('button', { disabled: props.disabled, onClick: () => emit('click') }, slots.default?.()) } })
const select = defineComponent({ props: ['modelValue'], emits: ['update:modelValue', 'change'], setup(props, { slots, emit }) { return () => h('select', { value: props.modelValue, onChange: (event: Event) => emit('change', (event.target as HTMLSelectElement).value) }, slots.default?.()) } })
const option = defineComponent({ props: ['label', 'value'], setup(props) { return () => h('option', { value: props.value }, props.label) } })
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/merchant/orders/:id', component: { template: '<div />' }, name: 'merchant-order-detail' }] })

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
    const wrapper = mount(MerchantOrdersPage, { global: { plugins: [router], stubs: { ElButton: button, ElSelect: select, ElOption: option } } })
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
})
