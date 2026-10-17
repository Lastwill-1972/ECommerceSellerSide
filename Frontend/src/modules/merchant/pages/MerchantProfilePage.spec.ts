import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MerchantProfilePage from './MerchantProfilePage.vue'
import { updateMerchantName } from '@/modules/merchant/api'

vi.mock('@/modules/merchant/api', () => ({ updateMerchantName: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn() } }))
const button = defineComponent({ props: ['disabled', 'loading'], emits: ['click'], setup(props, { slots, emit }) { return () => h('button', { disabled: props.disabled, onClick: () => emit('click') }, slots.default?.()) } })
const input = defineComponent({ props: ['modelValue'], emits: ['update:modelValue'], setup(props, { emit }) { return () => h('input', { value: props.modelValue, onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value) }) } })
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })

describe('merchant profile tenant scope', () => {
  it('discards a delayed profile response after the active merchant changes', async () => {
    setActivePinia(createPinia())
    const { useAuthStore } = await import('@/modules/auth/store')
    const auth = useAuthStore()
    auth.token = 'same-token'
    auth.user = { id: '5', username: 'admin', email: 'a@b.test', role: 'MERCHANT_ADMIN', merchant_id: '101', shop_id: null }
    auth.identityState = 'ready'
    let resolve!: (value: { id: string; name: string }) => void
    vi.mocked(updateMerchantName).mockReturnValue(new Promise((done) => { resolve = done }))
    const wrapper = mount(MerchantProfilePage, { global: { plugins: [router], stubs: { ElButton: button, ElInput: input } } })
    await wrapper.get('input').setValue('商家甲')
    await wrapper.find('button').trigger('click')
    auth.user = { ...auth.user!, merchant_id: '202' }
    resolve({ id: '202', name: '不应出现的旧响应名称' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('不应出现的旧响应名称')
    expect(wrapper.text()).not.toContain('当前服务端确认名称')
    wrapper.unmount()
  })
})
