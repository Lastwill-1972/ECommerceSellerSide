import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
import type { CurrentUser } from '@/shared/types/api'
import RoleFrame from './RoleFrame.vue'
const me = vi.hoisted(() => vi.fn())
vi.mock('@/modules/auth/api', () => ({ currentUserRequest: me }))

describe('role frame navigation during real store refresh', () => {
  it.each(['NONE', 'SHOP_OWNER'] as const)('leaves %s frame once when /me changes role, without a verification loop', async role => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    const old: CurrentUser = { id: '1', username: 'test', email: 't@example.com', role, merchant_id: role === 'NONE' ? null : '20', shop_id: role === 'NONE' ? null : '30' }
    auth.token = 'token'; auth.user = old; auth.identityState = 'ready'
    let server = old
    me.mockImplementation(async () => ({ ...server }))
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/old', component: RoleFrame, meta: { allowedRoles: [role] }, children: [{ path: '', component: { template: '<p>old-role-page</p>' } }] },
      { path: '/', name: 'dashboard', component: { template: '<h1>new-role-dashboard</h1>' } },
    ] })
    router.beforeEach(async () => { await auth.loadCurrentUser(true) })
    await router.push('/old')
    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
    server = { ...old, role: role === 'NONE' ? 'SHOP_OWNER' : 'SHOP_EMPLOYEE', merchant_id: '20', shop_id: '30' }
    me.mockClear()
    await auth.refreshIdentity(); await flushPromises(); await flushPromises()
    expect(router.currentRoute.value.name).toBe('dashboard')
    expect(wrapper.text()).toBe('new-role-dashboard')
    expect(me.mock.calls.length).toBeLessThanOrEqual(2)
    expect(auth.user?.role).toBe(server.role)
    wrapper.unmount()
  })

  it('does not restart navigation when a route guard discovers the old owner lost their role', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    let server: CurrentUser = { id: '1', username: 'test', email: 't@example.com', role: 'SHOP_OWNER', merchant_id: '20', shop_id: '30' }
    auth.token = 'token'; auth.user = server; auth.identityState = 'ready'
    me.mockImplementation(async () => ({ ...server }))
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/old', component: RoleFrame, meta: { allowedRoles: ['SHOP_OWNER'] }, children: [{ path: '', component: { template: '<p>old owner</p>' } }] },
      { path: '/', name: 'dashboard', component: { template: '<p>safe dashboard</p>' } },
    ] })
    router.beforeEach(async () => { await auth.loadCurrentUser(true) })
    await router.push('/old')
    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
    server = { ...server, role: 'NONE', merchant_id: null, shop_id: null }; me.mockClear()
    await router.push('/'); await flushPromises(); await flushPromises()
    expect(router.currentRoute.value.name).toBe('dashboard')
    expect(wrapper.text()).toBe('safe dashboard')
    expect(me.mock.calls.length).toBeLessThanOrEqual(2)
    wrapper.unmount()
  })
})
