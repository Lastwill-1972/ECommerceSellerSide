import { describe, it, expect, vi, beforeEach } from 'vitest'

/**
 * The audit module imports `http` from the real shared client; we replace that
 * transport with a spy so the real `queryParams` / `normalizeEventPage` exports
 * stay under test and no network is touched.
 */
const m = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/shared/http/client', () => ({ http: { get: m.get } }))

import * as api from './api'

function row(extra: Record<string, unknown> = {}) {
  return {
    id: '9007199254740993',
    actor_user_id: '9007199254740995',
    event_type: 'ORDER_DETAIL_READ',
    target_type: 'ORDER',
    target_id: '9007199254740997',
    outcome: 'SUCCESS',
    event_data: { note: 'ok' },
    created_at: '2026-10-17T10:00:00',
    ...extra,
  }
}

function pageOf(items: unknown[], extra: Record<string, unknown> = {}) {
  return { items, limit: 20, offset: 0, total: items.length, ...extra }
}

beforeEach(() => {
  m.get.mockReset()
  m.get.mockResolvedValue({ data: pageOf([row()]) })
})

describe('audit api — query payload', () => {
  it('defaults to limit 20 / offset 0 and returns the normalized page', async () => {
    const page = await api.auditEvents({})

    expect(m.get).toHaveBeenCalledTimes(1)
    const [url, config] = m.get.mock.calls[0]
    expect(url).toBe('/audit/events')
    expect(config.params).toEqual({ limit: 20, offset: 0 })
    expect(config).toMatchObject({ _suppressGlobalError: true })
    expect(page.limit).toBe(20)
    expect(page.offset).toBe(0)
    expect(page.total).toBe(1)
    expect(page.items).toHaveLength(1)
  })

  it('forwards every optional filter plus explicit paging, trimmed text preserved', async () => {
    await api.auditEvents({
      limit: 20,
      offset: 40,
      event_type: 'LOGIN',
      target_type: 'USER',
      start_at: '2026-10-17T00:00:00',
      end_at: '2026-10-18T23:59:59',
    })

    expect(m.get.mock.calls[0][1].params).toEqual({
      limit: 20,
      offset: 40,
      event_type: 'LOGIN',
      target_type: 'USER',
      start_at: '2026-10-17T00:00:00',
      end_at: '2026-10-18T23:59:59',
    })
  })

  it('drops empty optional filters but keeps { limit, offset }', async () => {
    expect(api.queryParams({ event_type: '', target_type: '', start_at: '', end_at: '' })).toEqual({
      limit: 20,
      offset: 0,
    })
    // Only empty strings are skipped; the raw value is forwarded without rewriting.
    expect(api.queryParams({ limit: 20, offset: 20, event_type: 'A B', target_type: 'SHOP' })).toEqual({
      limit: 20,
      offset: 20,
      event_type: 'A B',
      target_type: 'SHOP',
    })
  })

  it('never sends a tenant parameter, only the documented filter keys', async () => {
    await api.auditEvents({
      limit: 20,
      offset: 0,
      event_type: 'X',
      target_type: 'Y',
      start_at: '2026-10-17T00:00:00',
      end_at: '2026-10-17T01:00:00',
      // @ts-expect-error hostile extra tenant keys must not reach the wire
      merchant_id: '10',
      shop_id: '20',
      tenant_id: '1',
      target_id: '123',
    })

    const params = m.get.mock.calls[0][1].params as Record<string, unknown>
    expect(Object.keys(params).sort()).toEqual(['end_at', 'event_type', 'limit', 'offset', 'start_at', 'target_type'])
    for (const forbidden of ['merchant_id', 'shop_id', 'tenant_id', 'target_id']) {
      expect(params).not.toHaveProperty(forbidden)
    }

    const url = m.get.mock.calls[0][0] as string
    expect(url).toBe('/audit/events')
    expect(url).not.toMatch(/merchant|shop|tenant/)
  })
})

describe('audit api — BIGINT precision', () => {
  it('keeps beyond-safe-integer identifiers as exact decimal strings', async () => {
    const page = await api.auditEvents({})

    expect(page.items[0].id).toBe('9007199254740993')
    expect(page.items[0].actor_user_id).toBe('9007199254740995')
    expect(page.items[0].target_id).toBe('9007199254740997')
    expect(typeof page.items[0].id).toBe('string')
    expect(page.items[0].id).not.toBe('9007199254740992')
  })

  it('accepts numeric identifiers from the wire and keeps full precision as text', () => {
    const page = api.normalizeEventPage(pageOf([row({ id: 42, actor_user_id: '7', target_id: 9007199254740991 })]))
    expect(page.items[0].id).toBe('42')
    expect(page.items[0].actor_user_id).toBe('7')
    expect(page.items[0].target_id).toBe('9007199254740991')
  })

  it('rejects lossy or unsafe identifier values', () => {
    expect(() => api.normalizeEventPage(pageOf([row({ id: Number.MAX_SAFE_INTEGER + 2 })]))).toThrow()
    expect(() => api.normalizeEventPage(pageOf([row({ target_id: undefined })]))).toThrow()
  })

  it('coerces non-object or array event_data to an empty object', () => {
    const page = api.normalizeEventPage(
      pageOf([
        row({ event_data: 'text' }),
        row({ id: '2', event_data: [{ a: 1 }] }),
        row({ id: '3', event_data: null }),
      ]),
    )
    expect(page.items.map((item) => item.event_data)).toEqual([{}, {}, {}])
  })

  it('rejects malformed pages', () => {
    expect(() => api.normalizeEventPage(null)).toThrow()
    expect(() => api.normalizeEventPage({ items: [], limit: 'x', offset: 0, total: 0 })).toThrow()
    expect(() => api.normalizeEventPage({ limit: 20, offset: 0, total: 0 })).toThrow()
  })
})

describe('audit api — helpers', () => {
  it('validates naive datetime text without timezone markers', () => {
    expect(api.validNaiveDateTime('2026-10-17T10:00')).toBe(true)
    expect(api.validNaiveDateTime('2026-10-17T10:00:00')).toBe(true)
    expect(api.validNaiveDateTime('2026-10-17T10:00:00.123456')).toBe(true)
    expect(api.validNaiveDateTime('2026-10-17T10:00:00Z')).toBe(false)
    expect(api.validNaiveDateTime('2026-10-17T10:00:00+08:00')).toBe(false)
    expect(api.validNaiveDateTime('2026-10-17')).toBe(false)
  })

  it('bounds filter text to 1..100 chars after trim', () => {
    expect(api.validFilterText('ORDER')).toBe(true)
    expect(api.validFilterText('   ')).toBe(false)
    expect(api.validFilterText('a'.repeat(100))).toBe(true)
    expect(api.validFilterText('a'.repeat(101))).toBe(false)
  })
})
