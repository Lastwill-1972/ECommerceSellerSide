import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { router } from './router'
import { useAuthStore } from '@/modules/auth/store'
import { ApiError } from '@/shared/http/errors'

describe('merchant route role guard', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    await router.push('/login')
  })

  it('allows only a freshly confirmed merchant administrator into /merchant', async () => {
    const auth = useAuthStore()
    auth.token = 'fresh-token'
    auth.user = { id: '9007199254740995', username: 'merchant', email: 'm@example.test', role: 'MERCHANT_ADMIN', merchant_id: '9007199254740993', shop_id: null }
    auth.identityState = 'ready'
    vi.spyOn(auth, 'loadCurrentUser').mockResolvedValue(auth.user)
    await router.push('/merchant')
    expect(router.currentRoute.value.name).toBe('merchant-home')
    expect(auth.loadCurrentUser).toHaveBeenCalledWith(true)
  })

  it.each(['PLATFORM_ADMIN', 'SHOP_OWNER', 'SHOP_EMPLOYEE', 'NONE'] as const)('sends %s away from merchant-only pages', async (role) => {
    const auth = useAuthStore()
    auth.token = 'fresh-token'
    auth.user = { id: '9007199254740995', username: 'other', email: 'o@example.test', role, merchant_id: role === 'PLATFORM_ADMIN' ? null : '9007199254740993', shop_id: null }
    auth.identityState = 'ready'
    vi.spyOn(auth, 'loadCurrentUser').mockResolvedValue(auth.user)
    await router.push('/merchant/orders')
    expect(router.currentRoute.value.name).toBe('forbidden')
    expect(auth.loadCurrentUser).toHaveBeenCalledWith(true)
  })

  it('shows the expired-session reason after a guarded request receives HTTP 401', async () => {
    const auth = useAuthStore()
    auth.token = 'expired-token'
    vi.spyOn(auth, 'loadCurrentUser').mockRejectedValue(new ApiError('登录状态已失效。', 401))
    await router.push('/merchant/shops')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.reason).toBe('expired')
    expect(router.currentRoute.value.query.redirect).toBe('/merchant/shops')
  })

  it('preserves the expired-session reason from the HTTP interceptor for later navigation', async () => {
    const auth = useAuthStore()
    auth.token = null
    window.dispatchEvent(new CustomEvent('auth:expired'))
    await router.push('/merchant/shops')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.reason).toBe('expired')
    expect(router.currentRoute.value.query.redirect).toBe('/merchant/shops')
  })

  it('marks a guarded verification network error as unavailable', async () => {
    const auth = useAuthStore()
    auth.token = 'network-token'
    vi.spyOn(auth, 'loadCurrentUser').mockRejectedValue(new ApiError('服务暂时不可用。'))
    await router.push('/merchant/shops')
    expect(router.currentRoute.value.query.reason).toBe('unavailable')
  })

  it.each(['NONE', 'MERCHANT_ADMIN', 'SHOP_EMPLOYEE', 'PLATFORM_ADMIN'] as const)('rejects %s entering the shop work area directly', async role => {
    const auth = useAuthStore()
    auth.token = 'fresh-token'; auth.identityState = 'ready'
    auth.user = { id: '1', username: 'test', email: 't@example.test', role, merchant_id: '20', shop_id: '30' }
    vi.spyOn(auth, 'loadCurrentUser').mockResolvedValue(auth.user)
    await router.push('/shop/employees')
    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('admits the actual owner, then rejects the old role on next verification', async () => {
    const auth = useAuthStore()
    auth.token = 'fresh-token'; auth.identityState = 'ready'
    auth.user = { id: '1', username: 'test', email: 't@example.test', role: 'SHOP_OWNER', merchant_id: '20', shop_id: '30' }
    vi.spyOn(auth, 'loadCurrentUser').mockImplementation(async () => auth.user)
    await router.push('/shop/employees'); expect(router.currentRoute.value.name).toBe('shop-employees')
    auth.user = { ...auth.user, role: 'SHOP_EMPLOYEE' }
    await router.push('/shop/roles'); expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('lets only NONE access identity applications and does not grant privileges before approval', async () => {
    const auth = useAuthStore()
    auth.token = 'fresh-token'; auth.identityState = 'ready'
    auth.user = { id: '1', username: 'test', email: 't@example.test', role: 'NONE', merchant_id: null, shop_id: null }
    vi.spyOn(auth, 'loadCurrentUser').mockImplementation(async () => auth.user)
    await router.push('/applications'); expect(router.currentRoute.value.name).toBe('identity-applications')
    await router.push('/shop'); expect(router.currentRoute.value.name).toBe('forbidden')
    auth.user = { ...auth.user, role: 'SHOP_OWNER', merchant_id: '20', shop_id: '30' }
    await router.push('/applications'); expect(router.currentRoute.value.name).toBe('forbidden')
  })
})
