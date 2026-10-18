import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/shared/http/client', () => ({ http: { get, post } }))

import { buildCreateOrderBody, cancelOrder, completeOrder, createOrder } from '@/modules/orders/api'
import { stringifyApiJson } from '@/shared/http/precision'
import type { Order } from '@/shared/types/api'

const ORDER: Order = {
  id: '1',
  merchant_id: '2',
  shop_id: '3',
  created_by_user_id: '4',
  status: 'CANCELLED',
  total_amount: '19.90',
  created_at: '2026-10-18T10:00:00',
  completed_at: null,
  cancelled_at: null,
  items: [
    {
      id: '5',
      sku_id: '9007199254740997',
      quantity: '9007199254740999',
      unit_price: '0.01',
      subtotal: '90071992547409.99',
      sku_code_snapshot: 'S',
      spu_name_snapshot: '茶',
      specification_snapshot: '标准',
    },
  ],
} as unknown as Order

const KEY = '12345678-1234-4234-8234-123456789abc'

describe('orders api', () => {
  beforeEach(() => vi.clearAllMocks())
  it('builds bigint body serialized exactly by stringifyApiJson', () => {
    const body = buildCreateOrderBody([
      { sku_id: '9007199254740997', quantity: '9007199254740999' },
      { sku_id: 42, quantity: '9223372036854775807' },
    ])
    expect(typeof body.items[0].sku_id).toBe('bigint')
    expect(typeof body.items[0].quantity).toBe('bigint')
    expect(body.items[0].sku_id).toBe(9007199254740997n)
    expect(body.items[1].quantity).toBe(9223372036854775807n)
    expect(stringifyApiJson(body)).toBe(
      '{"items":[{"sku_id":9007199254740997,"quantity":9007199254740999},{"sku_id":42,"quantity":9223372036854775807}]}',
    )
  })

  it('rejects empty, duplicate, out-of-range and over-100 drafts', () => {
    expect(() => buildCreateOrderBody([])).toThrow()
    expect(() =>
      buildCreateOrderBody([
        { sku_id: '1', quantity: '1' },
        { sku_id: '1', quantity: '2' },
      ]),
    ).toThrow()
    expect(() => buildCreateOrderBody([{ sku_id: '9223372036854775808', quantity: '1' }])).toThrow()
    expect(() => buildCreateOrderBody([{ sku_id: '1', quantity: '0' }])).toThrow()
    const many = Array.from({ length: 101 }, (_, i) => ({ sku_id: String(i + 1), quantity: '1' }))
    expect(() => buildCreateOrderBody(many)).toThrow()
  })

  it('posts create order with exact body, idempotency key and transform', async () => {
    post.mockResolvedValueOnce({ data: ORDER })
    const result = await createOrder([{ sku_id: '9007199254740997', quantity: '9007199254740999' }], KEY)
    expect(post).toHaveBeenCalledTimes(1)
    const url = post.mock.calls[0][0]
    const body = post.mock.calls[0][1]
    const config = post.mock.calls[0][2]
    expect(url).toBe('/orders')
    expect(body.items[0].sku_id).toBe(9007199254740997n)
    expect(body.items[0].quantity).toBe(9007199254740999n)
    expect(config.headers['Idempotency-Key']).toBe(KEY)
    expect(config.transformRequest[0](body)).toBe(
      '{"items":[{"sku_id":9007199254740997,"quantity":9007199254740999}]}',
    )
    expect(result.id).toBe('1')
    expect(result.total_amount).toBe('19.90')
  })

  it('completes order reaching URL without idempotency header', async () => {
    post.mockResolvedValueOnce({ data: { ...ORDER, status: 'COMPLETED' } })
    const result = await completeOrder('7')
    expect(post.mock.calls[0][0]).toBe('/orders/7/complete')
    expect(post.mock.calls[0][2].headers).toEqual({ 'Content-Type': 'application/json' })
    expect(post.mock.calls[0][2].headers['Idempotency-Key']).toBeUndefined()
    expect(result.status).toBe('COMPLETED')
  })

  it('cancels order reaching URL without idempotency header', async () => {
    post.mockResolvedValueOnce({ data: ORDER })
    const result = await cancelOrder('9007199254740997')
    expect(post.mock.calls[0][0]).toBe('/orders/9007199254740997/cancel')
    expect(post.mock.calls[0][2].headers).toEqual({ 'Content-Type': 'application/json' })
    expect(result.status).toBe('CANCELLED')
  })

  it('replays cancel response without rewriting terminal status', async () => {
    const terminal = { ...ORDER, status: 'COMPLETED', cancelled_at: '2026-10-18T11:00:00' }
    post.mockResolvedValueOnce({ data: terminal })
    const result = await cancelOrder('1')
    expect(result.status).toBe('COMPLETED')
    expect(result.cancelled_at).toBe('2026-10-18T11:00:00')
  })
})
