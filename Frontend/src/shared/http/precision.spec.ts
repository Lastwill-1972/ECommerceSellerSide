import { describe, expect, it } from 'vitest'
import { formatDecimal, formatNaiveDateTime, parseApiJson } from './precision'

describe('API precision helpers', () => {
  it('keeps an integer larger than Number.MAX_SAFE_INTEGER as an exact decimal string', () => {
    const result = parseApiJson('{"id":9007199254740993,"count":7}') as { id: unknown; count: unknown }
    expect(String(result.id)).toBe('9007199254740993')
    expect(String(result.count)).toBe('7')
  })

  it('formats decimal amounts without rounding through Number', () => {
    expect(formatDecimal('12345678901234567890.5')).toBe('12,345,678,901,234,567,890.50')
    expect(formatDecimal('1.239')).toBe('1.239')
  })

  it('keeps database-local naive timestamps as wall-clock text', () => {
    expect(formatNaiveDateTime('2026-10-17T09:30:00')).toBe('2026-10-17 09:30:00')
  })
})
