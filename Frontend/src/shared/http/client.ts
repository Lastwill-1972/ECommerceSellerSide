import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { ApiError, toApiError } from './errors'
import { parseApiJson } from './precision'

declare module 'axios' {
  interface AxiosRequestConfig {
    _suppressGlobalError?: boolean
  }
  interface InternalAxiosRequestConfig {
    _authTokenAtRequest?: string | null
    _authGenerationAtRequest?: number
    _suppressGlobalError?: boolean
  }
}

type UnauthorizedHandler = (failedToken: string | null) => void
let unauthorizedHandler: UnauthorizedHandler = () => undefined
let toastHandler: ((error: ApiError) => void) | null = null
const toasted = new Set<string>()
let authGeneration = 0

export function bumpAuthGeneration(): void {
  authGeneration += 1
}

export function readStoredToken(): string | null {
  try { return sessionStorage.getItem('seller_access_token') } catch { return null }
}

export function writeStoredToken(token: string): void {
  try { sessionStorage.setItem('seller_access_token', token) }
  catch { throw new ApiError('浏览器无法保存本次登录状态，请启用会话存储后重试。', undefined, {}, 'storage') }
}

export function removeStoredToken(): void {
  try { sessionStorage.removeItem('seller_access_token') } catch { /* in-memory state still clears */ }
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

export function setHttpToastHandler(handler: ((error: ApiError) => void) | null): void {
  toastHandler = handler
}

export function clearHttpToastDedupe(): void {
  toasted.clear()
}

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 15_000,
  headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
  // Replace Axios' default JSON.parse so BIGINT tokens survive exactly.
  transformResponse: [parseApiJson],
})

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = readStoredToken()
  config._authTokenAtRequest = token
  config._authGenerationAtRequest = authGeneration
  if (token) config.headers.Authorization = `Bearer ${token}`
  else delete config.headers.Authorization
  return config
})

http.interceptors.response.use(
  (response) => response,
  (unknownError: unknown) => {
    if (axios.isAxiosError(unknownError)) {
      const config = unknownError.config
      const status = unknownError.response?.status
      if (status === 401 && config) {
        const currentToken = readStoredToken()
        // An old request must never clear a newer login session.
        if (currentToken && currentToken === config._authTokenAtRequest && config._authGenerationAtRequest === authGeneration) {
          unauthorizedHandler(config._authTokenAtRequest)
          window.dispatchEvent(new CustomEvent('auth:expired'))
        }
      }
    }
    const error = toApiError(unknownError)
    const config = axios.isAxiosError(unknownError) ? unknownError.config : undefined
    if (!config?._suppressGlobalError && error.code !== 'unauthorized' && toastHandler) {
      const key = `${error.status ?? error.code}:${error.message}`
      if (!toasted.has(key)) {
        toasted.add(key)
        toastHandler(error)
        window.setTimeout(() => toasted.delete(key), 1500)
      }
    }
    return Promise.reject(error)
  },
)

export function suppressGlobalError(config?: AxiosRequestConfig) {
  return { ...config, _suppressGlobalError: true }
}

export function assertResponse<T>(value: T | undefined): T {
  if (value === undefined) throw new ApiError('服务器返回了空响应。')
  return value
}
