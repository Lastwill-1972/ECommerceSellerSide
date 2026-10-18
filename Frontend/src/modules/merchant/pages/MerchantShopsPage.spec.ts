import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MerchantShopsPage from './MerchantShopsPage.vue'
import ButtonStub from '@/modules/merchant/test-support/ButtonStub'
import { listShops } from '@/modules/merchant/api'

vi.mock('@/modules/merchant/api', () => ({ listShops: vi.fn() }))

const shop = (id: string) => ({
  id, merchant_id: '10', name: `店铺 ${id}`, owner_user_id: '20', created_at: '2026-10-17T09:00:00',
})
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })

describe('merchant shops page', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'token'
    auth.identityState = 'ready'
    auth.user = { id: '5', username: 'admin', email: 'a@example.test', role: 'MERCHANT_ADMIN', merchant_id: '10', shop_id: null }
    vi.clearAllMocks()
  })

  it('requests the next page at offset 20 when 21 shops are available', async () => {
    vi.mocked(listShops).mockImplementation(async (limit, offset) => ({
      items: offset === 0 ? Array.from({ length: 20 }, (_, index) => shop(String(index + 1))) : [shop('21')],
      limit, offset, total: 21,
    }) as never)
    const wrapper = mount(MerchantShopsPage, { global: { plugins: [router], stubs: { ElButton: ButtonStub } } })
    await flushPromises()
    expect(wrapper.text()).toContain('共 21 家 · 第 1 页')
    await wrapper.findAll('button').find((button) => button.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(listShops).toHaveBeenLastCalledWith(20, 20)
    expect(wrapper.text()).toContain('店铺 21')
    expect(wrapper.text()).toContain('第 2 页')
    wrapper.unmount()
  })

  it('shows an explicit empty state when the merchant has no shops', async () => {
    vi.mocked(listShops).mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 } as never)
    const wrapper = mount(MerchantShopsPage, { global: { plugins: [router], stubs: { ElButton: ButtonStub } } })
    await flushPromises()
    expect(wrapper.text()).toContain('当前商家还没有店铺。')
    wrapper.unmount()
  })
})
