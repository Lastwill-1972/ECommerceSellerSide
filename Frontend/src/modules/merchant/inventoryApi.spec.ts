import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parseApiJson } from '@/shared/http/precision'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/shared/http/client', () => ({ http: mocks }))

import { adjustStock, getWarehouse, listMovements, listStocks } from './inventoryApi'

describe('inventory API adapters', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('parses warehouse, stock, and movement ids and quantities beyond JavaScript safe integers', async () => {
    mocks.get.mockResolvedValueOnce({ data: parseApiJson('{"id":9007199254740993,"merchant_id":9007199254740995,"name":"主仓","created_at":"2026-10-01T10:00:00"}') })
    await expect(getWarehouse()).resolves.toMatchObject({ id: '9007199254740993', merchant_id: '9007199254740995' })

    mocks.get.mockResolvedValueOnce({ data: parseApiJson(`{"items":[{"sku_id":9007199254740997,"sku_code":"LARGE","specification":"大包装","spu_id":9007199254740999,"spu_name":"茶","warehouse_id":9007199254740995,"quantity":9223372036854775807}],"limit":20,"offset":0,"total":1}`) })
    const stocks = await listStocks(20, 0)
    expect(stocks.items[0]).toMatchObject({ sku_id: '9007199254740997', spu_id: '9007199254740999', quantity: '9223372036854775807' })

    mocks.get.mockResolvedValueOnce({ data: parseApiJson(`{"items":[
      {"movement_id":9007199254741001,"sku_id":9007199254740997,"quantity_before":9223372036854775807,"quantity_delta":-1,"quantity_after":9223372036854775806,"idempotency_key":null,"merchant_id":9007199254740995,"warehouse_id":9007199254740995,"actor_user_id":9007199254740993,"operation_type":"ORDER_DEDUCT","order_item_id":9007199254741003,"created_at":"2026-10-01T10:00:00"},
      {"movement_id":9007199254741005,"sku_id":9007199254740997,"quantity_before":9223372036854775806,"quantity_delta":1,"quantity_after":9223372036854775807,"idempotency_key":"manual-key","merchant_id":9007199254740995,"warehouse_id":9007199254740995,"actor_user_id":9007199254740993,"operation_type":"MANUAL_ADJUSTMENT","order_item_id":null,"created_at":"2026-10-01T10:01:00"}
    ],"limit":20,"offset":0,"total":2}`) })
    const movements = await listMovements(20, 0)
    expect(movements.items).toMatchObject([
      { movement_id: '9007199254741001', quantity_before: '9223372036854775807', quantity_delta: '-1', quantity_after: '9223372036854775806', idempotency_key: null, order_item_id: '9007199254741003', operation_type: 'ORDER_DEDUCT' },
      { movement_id: '9007199254741005', quantity_before: '9223372036854775806', quantity_delta: '1', quantity_after: '9223372036854775807', idempotency_key: 'manual-key', order_item_id: null, operation_type: 'MANUAL_ADJUSTMENT' },
    ])
  })

  it('sends manual adjustment deltas as numbers and large SKU ids as exact JSON integers with an idempotency key', async () => {
    mocks.post.mockResolvedValueOnce({ data: parseApiJson('{"movement_id":9007199254741001,"sku_id":9007199254740997,"quantity_before":9223372036854775806,"quantity_delta":1,"quantity_after":9223372036854775807,"idempotency_key":"same-key"}') })
    const result = await adjustStock('9007199254740997', 1, 'same-key')
    expect(result).toMatchObject({ movement_id: '9007199254741001', sku_id: '9007199254740997', quantity_before: '9223372036854775806', quantity_delta: '1', quantity_after: '9223372036854775807', idempotency_key: 'same-key' })
    const [url, body, config] = mocks.post.mock.calls[0]
    expect(url).toBe('/inventory/operations')
    expect(config.headers['Idempotency-Key']).toBe('same-key')
    expect(config.transformRequest[0](body)).toBe('{"sku_id":9007199254740997,"delta":1}')
  })
})
