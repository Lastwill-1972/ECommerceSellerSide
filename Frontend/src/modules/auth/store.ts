import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiError } from '@/shared/http/errors'
import { bumpAuthGeneration, readStoredToken, removeStoredToken, setUnauthorizedHandler, writeStoredToken } from '@/shared/http/client'
import type { CurrentUser, LoginRequest, RegisterRequest, UserSummary } from '@/shared/types/api'
import { currentUserRequest, loginRequest, registerRequest } from './api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(readStoredToken())
  const user = ref<CurrentUser | null>(null)
  const identityState = ref<'unknown' | 'loading' | 'ready' | 'conflict' | 'unavailable'>('unknown')
  const scopeEpoch = ref(0)
  const isAuthenticated = computed(() => Boolean(token.value && user.value))
  let generation = 0
  let meInFlight: Promise<CurrentUser | null> | null = null

  function clearSession(expectedToken?: string | null): void {
    if (expectedToken !== undefined && token.value !== expectedToken) return
    generation += 1
    scopeEpoch.value += 1
    bumpAuthGeneration()
    token.value = null
    user.value = null
    identityState.value = 'unknown'
    meInFlight = null
    removeStoredToken()
  }

  setUnauthorizedHandler((failedToken) => clearSession(failedToken))

  async function loadCurrentUser(force = false): Promise<CurrentUser | null> {
    if (!token.value) return null
    if (!force && user.value && identityState.value === 'ready') return user.value
    if (meInFlight) return meInFlight
    const requestToken = token.value
    const requestGeneration = generation
    user.value = null
    identityState.value = 'loading'
    const pending = currentUserRequest()
      .then((current) => {
        if (generation !== requestGeneration || token.value !== requestToken) return null
        user.value = current
        identityState.value = 'ready'
        return current
      })
      .catch((error: unknown) => {
        if (generation !== requestGeneration || token.value !== requestToken) return null
        const status = error instanceof ApiError ? error.status : undefined
        if (status === 401) clearSession(requestToken)
        else if (status === 403) {
          user.value = null
          identityState.value = 'conflict'
        } else {
          user.value = null
          identityState.value = 'unavailable'
        }
        throw error
      })
      .finally(() => {
        if (meInFlight === pending) meInFlight = null
      })
    meInFlight = pending
    return pending
  }

  async function signIn(payload: LoginRequest): Promise<CurrentUser> {
    const loginAttempt = ++generation
    let response
    try {
      response = await loginRequest(payload)
    } catch (error) {
      if (generation !== loginAttempt) throw new ApiError('登录请求已被更新的操作取消。')
      throw error
    }
    if (generation !== loginAttempt) throw new ApiError('登录请求已被更新的操作取消。')
    bumpAuthGeneration()
    const thisGeneration = loginAttempt
    writeStoredToken(response.access_token)
    token.value = response.access_token
    user.value = null
    identityState.value = 'unknown'
    meInFlight = null
    try {
      const current = await loadCurrentUser(true)
      if (!current || generation !== thisGeneration) throw new ApiError('无法确认当前账号身份，请重试。')
      return current
    } catch (error) {
      if (generation === thisGeneration) clearSession(response.access_token)
      throw error
    }
  }

  async function signUp(payload: RegisterRequest): Promise<UserSummary> {
    return registerRequest(payload)
  }

  async function refreshIdentity(): Promise<CurrentUser | null> {
    scopeEpoch.value += 1
    user.value = null
    return loadCurrentUser(true)
  }

  function signOut(): void {
    clearSession()
  }

  return { token, user, identityState, scopeEpoch, isAuthenticated, loadCurrentUser, refreshIdentity, signIn, signUp, signOut }
})
