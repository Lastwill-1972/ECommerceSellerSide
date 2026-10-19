import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { router } from '@/app/router'
import { useAuthStore } from '@/modules/auth/store'
import type { IdentityRole } from '@/shared/types/api'

type AuthStore = ReturnType<typeof useAuthStore>

const SHOP_OWNER: IdentityRole = 'SHOP_OWNER'
const SHOP_EMPLOYEE: IdentityRole = 'SHOP_EMPLOYEE'
const NONE: IdentityRole = 'NONE'
const PLATFORM_ADMIN: IdentityRole = 'PLATFORM_ADMIN'
const MERCHANT_ADMIN: IdentityRole = 'MERCHANT_ADMIN'

function setupAuth(
  auth: AuthStore,
  role: IdentityRole,
  scope: { merchant_id: string | null; shop_id: string | null } = {
    merchant_id: '10',
    shop_id: '20',
  },
): void {
  auth.token = 'token'
  auth.identityState = 'ready'
  auth.user = {
    id: '5',
    username: 'test',
    email: 't@example.com',
    role,
    merchant_id: scope.merchant_id,
    shop_id: scope.shop_id,
  }
  vi.spyOn(auth, 'loadCurrentUser').mockImplementation(async () => auth.user)
}

async function expectAllowed(path: string): Promise<void> {
  await router.push(path)
  expect(router.currentRoute.value.path).toBe(path)
  expect(router.currentRoute.value.name).not.toBe('forbidden')
}

async function expectForbidden(path: string): Promise<void> {
  await router.push(path)
  expect(router.currentRoute.value.name).toBe('forbidden')
}

describe('roleGuard', () => {
  let auth: AuthStore

  beforeEach(async () => {
    setActivePinia(createPinia())
    auth = useAuthStore()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    if (router.currentRoute.value.name !== 'login') {
      await router.push('/login')
    }
    auth.token = ''
    auth.identityState = 'unknown'
    auth.user = null
  })

  it.each([
    [SHOP_OWNER, '/shop/orders'],
    [SHOP_EMPLOYEE, '/employee/orders'],
  ] as const)(
    'allows %s to open %s and keeps order paths unchanged',
    async (role, homePath) => {
      setupAuth(auth, role)

      await expectAllowed(homePath)
      expect(auth.loadCurrentUser).toHaveBeenCalled()

      await expectAllowed(`${homePath}/create`)
      expect(router.currentRoute.value.fullPath).toBe(`${homePath}/create`)

      await expectAllowed(`${homePath}/9007199254740997`)
      expect(router.currentRoute.value.params.id).toBe('9007199254740997')
    },
  )

  it.each([NONE, PLATFORM_ADMIN, MERCHANT_ADMIN] as const)(
    'forbids %s from /employee/orders',
    async (role) => {
      setupAuth(auth, role)

      await expectForbidden('/employee/orders')
      expect(auth.loadCurrentUser).toHaveBeenCalled()
    },
  )

  it('forbids employee from shop management routes', async () => {
    setupAuth(auth, SHOP_EMPLOYEE)

    await expectForbidden('/shop/employees')
    await expectForbidden('/shop/roles')
    await expectForbidden('/shop/warehouse')
    await expectForbidden('/shop/owner-transfer')
    expect(auth.loadCurrentUser).toHaveBeenCalled()
  })

  it.each([
    [MERCHANT_ADMIN, '/merchant/audit'],
    [SHOP_OWNER, '/shop/audit'],
  ] as const)('allows %s to its audit page %s', async (role, path) => {
    setupAuth(auth, role)
    await expectAllowed(path)
    expect(auth.loadCurrentUser).toHaveBeenCalled()
  })

  it.each([NONE, SHOP_EMPLOYEE, PLATFORM_ADMIN] as const)('rejects %s from both audit areas', async (role) => {
    setupAuth(auth, role)
    await expectForbidden('/merchant/audit')
    await expectForbidden('/shop/audit')
  })

  it('forbids employee when shop_id or merchant_id is null', async () => {
    setupAuth(auth, SHOP_EMPLOYEE, { merchant_id: '10', shop_id: null })
    await expectForbidden('/employee/orders')

    setupAuth(auth, SHOP_EMPLOYEE, { merchant_id: null, shop_id: '20' })
    await expectForbidden('/employee/orders')

    expect(auth.loadCurrentUser).toHaveBeenCalled()
  })

  it('honours server-side role change on the next navigation', async () => {
    setupAuth(auth, SHOP_EMPLOYEE)
    await expectAllowed('/employee/orders')

    auth.user = {
      id: '5',
      username: 'test',
      email: 't@example.com',
      role: SHOP_OWNER,
      merchant_id: '10',
      shop_id: '20',
    }
    vi.spyOn(auth, 'loadCurrentUser').mockImplementation(async () => auth.user)

    await expectForbidden('/employee/orders/create')
    await expectAllowed('/shop/orders')
    expect(auth.loadCurrentUser).toHaveBeenCalled()
  })
})
