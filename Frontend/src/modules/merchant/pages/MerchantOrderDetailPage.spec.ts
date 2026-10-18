import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import type { Order } from '@/shared/types/api'
import MerchantOrderDetailPage from './MerchantOrderDetailPage.vue'
import { getOrder } from '@/modules/merchant/api'
import ButtonStub from '@/modules/merchant/test-support/ButtonStub'

vi.mock('@/modules/merchant/api', () => ({ getOrder: vi.fn() }))

const order = (id: string): Order => ({
  id, merchant_id: '10', shop_id: '20', created_by_user_id: '30', status: 'PENDING', total_amount: '10.00',
  created_at: '2026-10-17T09:30:00', completed_at: null, cancelled_at: null, items: [],
})
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/merchant/orders', component: { template: '<div>订单列表</div>' }, name: 'merchant-orders' },
    { path: '/merchant/orders/:id', component: MerchantOrderDetailPage, name: 'detail' },
  ],
})

describe('merchant order detail route changes', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'token'
    auth.identityState = 'ready'
    auth.user = { id: '5', username: 'admin', email: 'a@example.test', role: 'MERCHANT_ADMIN', merchant_id: '10', shop_id: null }
    vi.clearAllMocks()
    await router.push('/merchant/orders/1')
    await router.isReady()
  })

  it('ignores a late response after the reused detail component changes order id', async () => {
    let resolveFirst!: (value: ReturnType<typeof order>) => void
    vi.mocked(getOrder).mockImplementation((id) => id === '1'
      ? new Promise((resolve) => { resolveFirst = resolve })
      : Promise.resolve(order(id)) as never)
    const wrapper = mount(RouterView, { global: { plugins: [router], stubs: { ElButton: ButtonStub } } })
    await router.push('/merchant/orders/2')
    await flushPromises()
    expect(wrapper.text()).toContain('编号：2')
    expect(wrapper.text()).toContain('订单总额快照')
    resolveFirst(order('1'))
    await flushPromises()
    expect(wrapper.text()).toContain('编号：2')
    expect(wrapper.text()).not.toContain('编号：1')
    expect(getOrder).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
