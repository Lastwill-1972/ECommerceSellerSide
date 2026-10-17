import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { router } from './router'
import { useAuthStore } from '@/modules/auth/store'

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
})
