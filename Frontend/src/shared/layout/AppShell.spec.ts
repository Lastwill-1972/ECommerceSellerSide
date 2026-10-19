import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AppShell from './AppShell.vue'
import { useAuthStore } from '@/modules/auth/store'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    {
      path: '/merchant', component: AppShell, children: [
        { path: '', name: 'merchant-home', component: { template: '<div>merchant home</div>' } },
        { path: 'profile', name: 'merchant-profile', component: { template: '<div />' } },
        { path: 'shops', name: 'merchant-shops', component: { template: '<div />' } },
        { path: 'applications', name: 'merchant-applications', component: { template: '<div />' } },
        { path: 'orders', name: 'merchant-orders', component: { template: '<div />' } },
        { path: 'catalog', name: 'merchant-catalog', component: { template: '<div />' } },
        { path: 'inventory', name: 'merchant-inventory', component: { template: '<div />' } },
        { path: 'owner-transfer', name: 'merchant-owner-transfer', component: { template: '<div />' } },
        { path: 'audit', name: 'merchant-audit', component: { template: '<div />' } },
      ],
    },
    { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
    { path: '/', name: 'dashboard', component: { template: '<div>dashboard</div>' } },
  ],
})
const stubs = {
  ElIcon: { template: '<span><slot /></span>' },
  ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' },
  ElDropdownMenu: { template: '<div><slot /></div>' },
  ElDropdownItem: { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
  DataBoard: true, Lock: true, ArrowRight: true, ArrowDown: true, SwitchButton: true, Shop: true, Tickets: true,
}

describe('AppShell logout behavior', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.token = 'valid-token'
    auth.identityState = 'ready'
    auth.user = { id: '1', username: 'merchant', email: 'm@example.test', role: 'MERCHANT_ADMIN', merchant_id: '2', shop_id: null }
    await router.push('/merchant')
    await router.isReady()
  })

  it('does not label an intentional logout as an expired session', async () => {
    const wrapper = mount(RouterView, { global: { plugins: [router], stubs: { ...stubs, RouterLink: false } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().includes('退出登录'))!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.reason).toBeUndefined()
    wrapper.unmount()
  })
})
