import { describe, expect, it } from 'vitest'
import { normalizeCurrentUser, normalizeUser } from './api'
import { parseApiJson } from '@/shared/http/precision'

describe('auth response adapters', () => {
  it('normalizes BIGINT identifiers to strings for all backend roles', () => {
    const current = normalizeCurrentUser({
      id: 9007199254740993n,
      username: 'seller',
      email: 'seller@example.com',
      role: 'SHOP_OWNER',
      merchant_id: 123,
      shop_id: null,
    })
    expect(current.id).toBe('9007199254740993')
    expect(current.merchant_id).toBe('123')
    expect(current.shop_id).toBeNull()
  })

  it('converts safe numeric IDs and rejects unsafe rounded numbers', () => {
    expect(normalizeUser({ id: 19, username: 'seller', email: 'seller@example.com' }).id).toBe('19')
    expect(() => normalizeUser({ id: 9007199254740992, username: 'seller', email: 'seller@example.com' })).toThrow('不是安全整数')
    const user = parseApiJson('{"id":9007199254740993,"username":"seller","email":"seller@example.com"}')
    expect(normalizeUser(user).id).toBe('9007199254740993')
  })

  it('rejects unknown roles and malformed identity payloads', () => {
    expect(() => normalizeCurrentUser({ id: 1, username: 'a', email: 'a@b.test', role: 'ROOT' })).toThrow('未知的身份类型')
    expect(() => normalizeUser({ username: 'missing-fields' })).toThrow('缺少必要字段')
  })
})
