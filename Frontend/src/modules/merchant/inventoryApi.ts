import { http } from '@/shared/http/client'
import { normalizeId } from '@/modules/auth/api'
import { stringifyApiJson } from '@/shared/http/precision'
import type { Page, Stock, StockMovement, Warehouse } from '@/shared/types/api'

const noToast = { _suppressGlobalError: true }
const exactConfig = { ...noToast, transformRequest: [stringifyApiJson], headers: { 'Content-Type': 'application/json' } }
function record(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) throw new Error(message)
  return value as Record<string, unknown>
}
function integer(value: unknown, field: string): string {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new Error(`服务端返回的 ${field} 超出安全整数范围。`)
    return String(value)
  }
  if (typeof value === 'bigint') return value.toString()
  if (typeof value === 'string' && /^-?\d+$/.test(value)) return BigInt(value).toString()
  throw new Error(`服务端返回的 ${field} 格式不正确。`)
}
function positive(value: unknown, field: string) { return normalizeId(value, field) }
function page<T>(value: unknown, parse: (row: unknown) => T): Page<T> {
  const raw = record(value, '服务器返回的列表格式不正确。')
  if (!Array.isArray(raw.items) || !Number.isSafeInteger(raw.limit) || !Number.isSafeInteger(raw.offset) || !Number.isSafeInteger(raw.total)) throw new Error('服务器返回的分页信息格式不正确。')
  return { items: raw.items.map(parse), limit: raw.limit as number, offset: raw.offset as number, total: raw.total as number }
}
function parseStock(value: unknown): Stock {
  const row = record(value, '库存数据格式不正确。')
  return {
    ...row, sku_id: positive(row.sku_id, 'SKU 编号'), spu_id: positive(row.spu_id, '商品编号'),
    warehouse_id: positive(row.warehouse_id, '仓库编号'), quantity: integer(row.quantity, '库存数量'),
  } as unknown as Stock
}
function parseMovement(value: unknown): StockMovement {
  const row = record(value, '库存流水格式不正确。')
  return {
    ...row, movement_id: positive(row.movement_id, '流水编号'), sku_id: positive(row.sku_id, 'SKU 编号'),
    quantity_before: integer(row.quantity_before, '操作前库存'), quantity_delta: integer(row.quantity_delta, '库存变化'),
    quantity_after: integer(row.quantity_after, '操作后库存'), merchant_id: positive(row.merchant_id, '商家编号'),
    warehouse_id: positive(row.warehouse_id, '仓库编号'), actor_user_id: positive(row.actor_user_id, '操作人编号'),
    order_item_id: row.order_item_id === null ? null : positive(row.order_item_id, '订单明细编号'),
    idempotency_key: row.idempotency_key === null ? null : String(row.idempotency_key),
  } as unknown as StockMovement
}
export async function getWarehouse(): Promise<Warehouse> {
  const { data } = await http.get('/inventory/warehouse', noToast)
  const row = record(data, '仓库数据格式不正确。')
  return { ...row, id: positive(row.id, '仓库编号'), merchant_id: positive(row.merchant_id, '商家编号') } as unknown as Warehouse
}
export async function listStocks(limit: number, offset: number) {
  const { data } = await http.get('/inventory/stocks', { ...noToast, params: { limit, offset } })
  return page(data, parseStock)
}
export async function listMovements(limit: number, offset: number, skuId?: string) {
  const { data } = await http.get('/inventory/movements', { ...noToast, params: { limit, offset, ...(skuId ? { sku_id: skuId } : {}) } })
  return page(data, parseMovement)
}
export interface StockOperationResult {
  movement_id: string; sku_id: string; quantity_before: string; quantity_delta: string; quantity_after: string; idempotency_key: string
}
function parseOperation(value: unknown): StockOperationResult {
  const row = record(value, '库存操作响应格式不正确。')
  if (typeof row.idempotency_key !== 'string') throw new Error('库存操作响应缺少幂等键。')
  return {
    movement_id: positive(row.movement_id, '流水编号'), sku_id: positive(row.sku_id, 'SKU 编号'),
    quantity_before: integer(row.quantity_before, '操作前库存'), quantity_delta: integer(row.quantity_delta, '库存变化'),
    quantity_after: integer(row.quantity_after, '操作后库存'), idempotency_key: row.idempotency_key,
  }
}
export async function adjustStock(skuId: string, delta: number, key: string): Promise<StockOperationResult> {
  const { data } = await http.post('/inventory/operations', { sku_id: BigInt(skuId), delta }, {
    ...exactConfig, headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
  })
  return parseOperation(data)
}
