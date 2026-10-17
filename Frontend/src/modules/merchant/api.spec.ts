import { describe, expect, it } from 'vitest'
import { normalizeOrder, normalizeShop, normalizeWarehouseApplication } from './api'
import { parseApiJson } from '@/shared/http/precision'

describe('merchant API response adapters', () => {
  it('keeps BIGINT ids and order quantity exact decimal strings', () => {
    const order = normalizeOrder(parseApiJson(`{
      "id": 9007199254740993, "merchant_id": 9007199254740995, "shop_id": 9007199254740997,
      "created_by_user_id": 9007199254740999, "status": "PENDING", "total_amount": "12345678901234567890.10",
      "created_at": "2026-10-17T09:30:00", "completed_at": null, "cancelled_at": null,
      "items": [{ "id": 9007199254741001, "sku_id": 9007199254741003, "quantity": 9223372036854775807,
        "unit_price": "999999999999.99", "subtotal": "999999999999999999.99", "sku_code_snapshot": "SKU",
        "spu_name_snapshot": "历史名称", "specification_snapshot": "历史规格" }]
    }`))
    expect(order.id).toBe('9007199254740993')
    expect(order.items[0].quantity).toBe('9223372036854775807')
    expect(order.total_amount).toBe('12345678901234567890.10')
    expect(order.items[0].subtotal).toBe('999999999999999999.99')
  })

  it('normalizes shop and warehouse application ids from precise numeric tokens', () => {
    const shop = parseApiJson('{"id":9007199254740993,"merchant_id":9007199254740995,"owner_user_id":9007199254740997,"name":"店","created_at":"2026-10-17T09:00:00"}')
    const application = parseApiJson('{"id":9007199254740993,"shop_id":9007199254740995,"applicant_user_id":9007199254740997,"reviewer_user_id":null}')
    expect(normalizeShop(shop).owner_user_id).toBe('9007199254740997')
    expect(normalizeWarehouseApplication(application).shop_id).toBe('9007199254740995')
  })

  it('rejects unsafe numbers and malformed money instead of rounding or recalculating', () => {
    expect(() => normalizeShop({ id: Number.MAX_SAFE_INTEGER + 1, merchant_id: '1', owner_user_id: '2' })).toThrow()
    expect(() => normalizeOrder({ id: '1', merchant_id: '2', shop_id: '3', created_by_user_id: '4', total_amount: 1.2, items: [] })).toThrow()
  })
})
