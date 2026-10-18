import { http } from '@/shared/http/client'
import { stringifyApiJson } from '@/shared/http/precision'
import { normalizeOrder } from '@/modules/merchant/api'
import { normalizeOrderItems, type OrderItemDraft } from '@/modules/orders/pendingOrder'
import type { Order } from '@/shared/types/api'

const noToast = { _suppressGlobalError: true }
const exactConfig = { ...noToast, transformRequest: [stringifyApiJson], headers: { 'Content-Type': 'application/json' } }

/** A single order line as accepted by the backend create-order contract. */
export type OrderLineInput = OrderItemDraft

/**
 * Build the exact create-order body. Validation (1..100 distinct lines, ids/quantities within the
 * signed BIGINT range, no duplicate SKU) is delegated to `normalizeOrderItems` so the draft and the
 * request share one rule set. Only `{ items: [{ sku_id, quantity }] }` is sent and both identifiers
 * are emitted as `bigint` so `stringifyApiJson` serializes them as lossless JSON NUMBER tokens.
 */
export function buildCreateOrderBody(items: readonly OrderLineInput[]): { items: { sku_id: bigint; quantity: bigint }[] } {
  const normalized = normalizeOrderItems(items)
  return {
    items: normalized.map((item) => ({
      sku_id: BigInt(item.sku_id),
      quantity: BigInt(item.quantity),
    })),
  }
}

/** Send the create-order request; amounts in the response come from the server verbatim. */
export async function createOrder(items: readonly OrderLineInput[], key: string): Promise<Order> {
  if (typeof key !== 'string' || key.length === 0) throw new Error('下单缺少幂等键，请稍后重试。')
  const body = buildCreateOrderBody(items)
  const { data } = await http.post('/orders', body, {
    ...exactConfig,
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
  })
  // Amounts come from the server verbatim; this module never recalculates money.
  return normalizeOrder(data)
}

function orderIdPath(id: string | number | bigint, action: 'complete' | 'cancel'): string {
  const normalized = normalizeOrderItems([{ sku_id: id, quantity: 1n }])[0]
  if (normalized === undefined) throw new Error('订单编号不正确。')
  return `/orders/${normalized.sku_id}/${action}`
}

export async function completeOrder(id: string | number | bigint): Promise<Order> {
  // Completion is not idempotency-keyed: no `Idempotency-Key` header is sent.
  const { data } = await http.post(orderIdPath(id, 'complete'), {}, exactConfig)
  return normalizeOrder(data)
}

export async function cancelOrder(id: string | number | bigint): Promise<Order> {
  // Cancellation sends no `Idempotency-Key`; the server's response is authoritative.
  const { data } = await http.post(orderIdPath(id, 'cancel'), {}, exactConfig)
  // A cancel replay of an already-terminal order must not be rewritten to a different status.
  return normalizeOrder(data)
}
