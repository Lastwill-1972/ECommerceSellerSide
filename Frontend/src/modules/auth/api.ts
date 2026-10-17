import { http, suppressGlobalError } from '@/shared/http/client'
import type { CurrentUser, LoginRequest, LoginResponse, RegisterRequest, UserSummary } from '@/shared/types/api'

const roles = new Set(['PLATFORM_ADMIN', 'MERCHANT_ADMIN', 'SHOP_OWNER', 'SHOP_EMPLOYEE', 'NONE'])
export function normalizeId(value: unknown, field: string): string {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`服务端返回的 ${field} 不是安全整数。`)
    return String(value)
  }
  if (typeof value === 'bigint') {
    if (value <= 0n) throw new Error(`服务端返回的 ${field} 必须为正整数。`)
    return value.toString()
  }
  if (typeof value === 'string' && /^[1-9]\d*$/.test(value)) return value
  throw new Error(`服务端返回的 ${field} 格式不正确。`)
}

export function normalizeUser<T extends UserSummary>(value: unknown): T {
  if (typeof value !== 'object' || value === null) throw new Error('服务器返回的账号信息格式不正确。')
  const user = value as Record<string, unknown>
  if (typeof user.username !== 'string' || typeof user.email !== 'string') {
    throw new Error('服务器返回的账号信息缺少必要字段。')
  }
  return { ...user, id: normalizeId(user.id, '账号编号') } as T
}

export async function loginRequest(payload: LoginRequest): Promise<LoginResponse> {
  const { data } = await http.post<LoginResponse>('/auth/login', payload, suppressGlobalError())
  if (typeof data.access_token !== 'string' || data.token_type !== 'bearer') throw new Error('服务器返回的登录凭据格式不正确。')
  return { ...data, user: normalizeUser(data.user) }
}

export async function registerRequest(payload: RegisterRequest): Promise<UserSummary> {
  const { data } = await http.post<UserSummary>('/auth/register', payload, suppressGlobalError())
  return normalizeUser<UserSummary>(data)
}

export function normalizeCurrentUser(value: unknown): CurrentUser {
  const user = normalizeUser<CurrentUser>(value)
  if (typeof user.role !== 'string' || !roles.has(user.role)) throw new Error('服务端返回了未知的身份类型。')
  return {
    ...user,
    merchant_id: user.merchant_id === null ? null : normalizeId(user.merchant_id, '商家编号'),
    shop_id: user.shop_id === null ? null : normalizeId(user.shop_id, '店铺编号'),
  }
}

export async function currentUserRequest(): Promise<CurrentUser> {
  const { data } = await http.get<CurrentUser>('/auth/me', suppressGlobalError())
  return normalizeCurrentUser(data)
}
