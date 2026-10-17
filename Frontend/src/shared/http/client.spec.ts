import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { bumpAuthGeneration, http, setUnauthorizedHandler } from './client'

function mockBrowserToken(token: string) {
  const values = new Map([['seller_access_token', token]])
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
  vi.stubGlobal('window', { dispatchEvent: vi.fn() })
  vi.stubGlobal('CustomEvent', class CustomEvent { constructor(readonly type: string) {} })
}

describe('HTTP authorization expiry', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setUnauthorizedHandler(() => undefined)
    mockBrowserToken('active-token')
  })

  it('clears the active session and dispatches auth:expired after a current-token 401', async () => {
    const expired = vi.fn()
    setUnauthorizedHandler(expired)
    await expect(http.get('/auth/me', {
      adapter: (config) => Promise.reject(new axios.AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined, {
        status: 401, statusText: 'Unauthorized', headers: {}, config, data: {},
      })),
    })).rejects.toMatchObject({ status: 401 })
    expect(expired).toHaveBeenCalledWith('active-token')
    expect(window.dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'auth:expired' }))
  })

  it('ignores a delayed 401 from an older auth generation', async () => {
    const expired = vi.fn()
    setUnauthorizedHandler(expired)
    await expect(http.get('/slow', {
      adapter: async (config) => {
        bumpAuthGeneration()
        throw new axios.AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined, {
          status: 401, statusText: 'Unauthorized', headers: {}, config, data: {},
        })
      },
    })).rejects.toMatchObject({ status: 401 })
    expect(expired).not.toHaveBeenCalled()
    expect(window.dispatchEvent).not.toHaveBeenCalled()
  })
})
