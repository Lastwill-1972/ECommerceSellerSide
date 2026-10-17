import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { CurrentUser, LoginResponse } from '@/shared/types/api'

const mocks = vi.hoisted(() => ({
  loginRequest: vi.fn<(payload: { identifier: string; password: string }) => Promise<LoginResponse>>(),
  currentUserRequest: vi.fn<() => Promise<CurrentUser>>(),
  registerRequest: vi.fn(),
}))

vi.mock('./api', () => mocks)

import { useAuthStore } from './store'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function mockSessionStorage() {
  const values = new Map<string, string>()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
  return values
}

const response = (token: string): LoginResponse => ({
  access_token: token,
  token_type: 'bearer',
  user: { id: '1', username: 'seller', email: 'seller@example.com' },
})

const currentUser: CurrentUser = {
  id: '1', username: 'seller', email: 'seller@example.com', role: 'NONE', merchant_id: null, shop_id: null,
}

describe('auth session races', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.loginRequest.mockReset()
    mocks.currentUserRequest.mockReset().mockResolvedValue(currentUser)
    mocks.registerRequest.mockReset()
    mockSessionStorage()
  })

  it('does not restore a login response after the user signed out', async () => {
    const pending = deferred<LoginResponse>()
    mocks.loginRequest.mockReturnValueOnce(pending.promise)
    const store = useAuthStore()
    const login = store.signIn({ identifier: 'seller', password: 'password' })
    store.signOut()
    pending.resolve(response('late-token'))
    await expect(login).rejects.toThrow('更新的操作取消')
    expect(store.token).toBeNull()
    expect(sessionStorage.getItem('seller_access_token')).toBeNull()
  })

  it('keeps the newest login when login responses arrive out of order', async () => {
    const older = deferred<LoginResponse>()
    const newer = deferred<LoginResponse>()
    mocks.loginRequest.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    const store = useAuthStore()
    const first = store.signIn({ identifier: 'first', password: 'password' })
    const second = store.signIn({ identifier: 'second', password: 'password' })
    newer.resolve(response('new-token'))
    await expect(second).resolves.toEqual(currentUser)
    older.resolve(response('old-token'))
    await expect(first).rejects.toThrow('更新的操作取消')
    expect(store.token).toBe('new-token')
    expect(sessionStorage.getItem('seller_access_token')).toBe('new-token')
  })
})
