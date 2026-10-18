/**
 * Pending (drafted) order persistence.
 *
 * A pending order is stored in `sessionStorage` under a scope derived from the 3 identity ids so
 * that two merchants/shops/users sharing a browser never observe each other's draft. Nothing here
 * touches tokens/passwords: the record only keeps the idempotency key, the ordered SKU/quantity
 * draft and the scope ids.
 *
 * States are `unknown` (draft written, outcome not yet known) and `submitting` (request in flight).
 * Neither is terminal: the record only tracks an un-resolved local draft and says nothing about the
 * business outcome of the order.
 *
 * Failure policy: corrupted or unreadable records and failed writes throw explicitly. A record is
 * never silently dropped, and because losing the stored records must not be reported as an unknown
 * outcome, this module only ever reports honest outcomes (the page layer owns user-facing notices).
 * In particular a missing/corrupted record NEVER means the order failed.
 */

export interface PendingOrderItem {
  sku_id: string
  quantity: string
}

export type PendingOrderState = 'unknown' | 'submitting'

export interface PendingOrderScope {
  user_id: string
  merchant_id: string
  shop_id: string
}

export interface PendingOrder {
  key: string
  items: PendingOrderItem[]
  user_id: string
  merchant_id: string
  shop_id: string
  state: PendingOrderState
  created_at: string
}

export interface PendingOrderStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

/** Signed BIGINT upper bound (2^63-1); the lower bound is 1 (positive integers only). */
export const MAX_QUANTITY = 9223372036854775807n
export const MAX_DISTINCT_SKUS = 100
const STORAGE_PREFIX = 'seller.pending-order.v1'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const QUANTITY_PATTERN = /^[1-9]\d*$/

export class PendingOrderStorageError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message)
    this.name = 'PendingOrderStorageError'
  }
}

export function isValidUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value)
}

/** Parse a positive integer (string or safe-range number) inside 1..2^63-1 and return exact text. */
export function parsePositiveInteger(value: unknown, field: string): string {
  if (typeof value === 'string') {
    if (!QUANTITY_PATTERN.test(value)) throw new Error(`${field}必须是正整数。`)
    const parsed = BigInt(value)
    if (parsed > MAX_QUANTITY) throw new Error(`${field}超出允许范围。`)
    return parsed.toString()
  }
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${field}必须是安全范围内的正整数。`)
    return String(value)
  }
  throw new Error(`${field}必须是正整数。`)
}

export function parseSkuId(value: unknown): string {
  return parsePositiveInteger(value, 'SKU 编号')
}

export function parseQuantity(value: unknown): string {
  return parsePositiveInteger(value, '数量')
}

export interface OrderItemDraft {
  sku_id: string | number | bigint
  quantity: string | number | bigint
}

/**
 * Validate a 1..100 line, duplicate-free draft and normalize both fields to exact decimal text.
 * Each value must be within 1..2^63-1; order is preserved and nothing is recalculated.
 */
export function normalizeOrderItems(items: readonly OrderItemDraft[]): PendingOrderItem[] {
  if (!Array.isArray(items) || items.length === 0) throw new Error('请至少选择一个商品。')
  if (items.length > MAX_DISTINCT_SKUS) throw new Error(`一次最多提交 ${MAX_DISTINCT_SKUS} 个不同商品。`)
  const seen = new Set<string>()
  const result: PendingOrderItem[] = []
  for (const [index, item] of items.entries()) {
    if (typeof item !== 'object' || item === null) throw new Error(`第 ${index + 1} 行商品格式不正确。`)
    const skuId = parsePositiveInteger(
      typeof item.sku_id === 'bigint' ? item.sku_id.toString() : item.sku_id,
      `第 ${index + 1} 行 SKU 编号`,
    )
    const quantity = parsePositiveInteger(
      typeof item.quantity === 'bigint' ? item.quantity.toString() : item.quantity,
      `第 ${index + 1} 行数量`,
    )
    if (seen.has(skuId)) throw new Error(`第 ${index + 1} 行 SKU 重复，请合并相同商品。`)
    seen.add(skuId)
    result.push({ sku_id: skuId, quantity })
  }
  return result
}

export function parseScope(scope: PendingOrderScope): PendingOrderScope {
  if (typeof scope !== 'object' || scope === null) throw new Error('缺少待确认订单的账号范围。')
  return {
    user_id: parsePositiveInteger(scope.user_id, '用户编号'),
    merchant_id: parsePositiveInteger(scope.merchant_id, '商家编号'),
    shop_id: parsePositiveInteger(scope.shop_id, '店铺编号'),
  }
}

/** Storage key isolating drafts by user, merchant and shop. */
export function scopeStorageKey(scope: PendingOrderScope): string {
  const parsed = parseScope(scope)
  return `${STORAGE_PREFIX}:${parsed.user_id}:${parsed.merchant_id}:${parsed.shop_id}`
}

function parseDateTime(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0 || Number.isNaN(Date.parse(value))) {
    throw new Error('待确认订单的创建时间格式不正确。')
  }
  return value
}

/** Strict whitelist validation of a persisted record; unknown extra fields are rejected. */
export function parsePendingOrderRecord(raw: unknown): PendingOrder {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new Error('待确认订单记录已损坏。')
  const record = raw as Record<string, unknown>
  const allowed = new Set(['key', 'items', 'user_id', 'merchant_id', 'shop_id', 'state', 'created_at'])
  for (const field of Object.keys(record)) {
    if (!allowed.has(field)) throw new Error(`待确认订单记录包含未知字段 ${field}。`)
  }
  for (const field of allowed) {
    if (!(field in record)) throw new Error(`待确认订单记录缺少字段 ${field}。`)
  }
  if (!isValidUuid(record.key)) throw new Error('待确认订单的幂等键格式不正确。')
  if (record.state !== 'unknown' && record.state !== 'submitting') throw new Error('待确认订单状态不正确。')
  if (!Array.isArray(record.items)) throw new Error('待确认订单明细格式不正确。')
  const items = normalizeOrderItems(
    record.items.map((item) => {
      if (typeof item !== 'object' || item === null) throw new Error('待确认订单明细格式不正确。')
      const line = item as Record<string, unknown>
      for (const field of Object.keys(line)) {
        if (field !== 'sku_id' && field !== 'quantity') throw new Error(`待确认订单明细包含未知字段 ${field}。`)
      }
      if (!('sku_id' in line) || !('quantity' in line)) throw new Error('待确认订单明细缺少字段。')
      return { sku_id: line.sku_id as string, quantity: line.quantity as string }
    }),
  )
  return {
    key: record.key,
    items,
    user_id: parsePositiveInteger(record.user_id, '用户编号'),
    merchant_id: parsePositiveInteger(record.merchant_id, '商家编号'),
    shop_id: parsePositiveInteger(record.shop_id, '店铺编号'),
    state: record.state,
    created_at: parseDateTime(record.created_at),
  }
}

function defaultStorage(): PendingOrderStorage {
  if (typeof sessionStorage === 'undefined' || sessionStorage === null) {
    throw new PendingOrderStorageError('当前浏览器不支持会话存储，无法保存待确认订单。')
  }
  return sessionStorage as unknown as PendingOrderStorage
}

/** Message for local records that were lost or corrupted: never asserts a business failure. */
const CORRUPTED_RECORD_MESSAGE = '待确认订单本地记录已丢失或损坏，请先人工核对订单，禁止直接重复下单。'

/**
 * Read the pending order for one scope. A corrupted record throws instead of being discarded, so a
 * possibly submitted order is never lost without the caller noticing. The error asks for manual
 * reconciliation and never claims the order failed.
 */
export function readPendingOrder(scope: PendingOrderScope, storage?: PendingOrderStorage): PendingOrder | null {
  const key = scopeStorageKey(scope)
  const store = storage ?? defaultStorage()
  let text: string | null
  try {
    text = store.getItem(key)
  } catch (error) {
    throw new PendingOrderStorageError('读取待确认订单失败，请检查浏览器存储设置。', error)
  }
  if (text === null || text === undefined) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(text) as unknown
  } catch (error) {
    throw new PendingOrderStorageError(CORRUPTED_RECORD_MESSAGE, error)
  }
  const pending = parsePendingOrderRecord(parsed)
  const expected = parseScope(scope)
  if (pending.user_id !== expected.user_id || pending.merchant_id !== expected.merchant_id || pending.shop_id !== expected.shop_id) {
    throw new Error('待确认订单记录与当前账号范围不一致，已停止读取。')
  }
  return pending
}

/** Persist a draft in the `unknown` state with a fresh caller-supplied UUID idempotency key. */
export function savePendingOrder(
  input: {
    key: string
    items: readonly OrderItemDraft[]
    user_id: string | number | bigint
    merchant_id: string | number | bigint
    shop_id: string | number | bigint
    created_at?: string
  },
  storage?: PendingOrderStorage,
): PendingOrder {
  if (!isValidUuid(input.key)) throw new Error('幂等键必须是 UUID 格式。')
  const scope = parseScope({
    user_id: input.user_id as string,
    merchant_id: input.merchant_id as string,
    shop_id: input.shop_id as string,
  })
  // Store freshly built plain values so later caller mutation cannot alter what was persisted.
  const pending: PendingOrder = {
    key: input.key,
    items: normalizeOrderItems(input.items),
    user_id: scope.user_id,
    merchant_id: scope.merchant_id,
    shop_id: scope.shop_id,
    state: 'unknown',
    created_at: input.created_at ?? new Date().toISOString(),
  }
  parseDateTime(pending.created_at)
  const store = storage ?? defaultStorage()
  try {
    store.setItem(scopeStorageKey(scope), JSON.stringify(pending))
  } catch (error) {
    throw new PendingOrderStorageError('保存待确认订单失败，请检查浏览器存储空间后重试。', error)
  }
  return parsePendingOrderRecord(pending)
}

/**
 * Change the local state of an existing non-terminal draft; the record must still match the scope.
 * `unknown`/`submitting` describe the local request tracking only, not the order business outcome.
 */
export function updatePendingOrderState(
  scope: PendingOrderScope,
  state: PendingOrderState,
  storage?: PendingOrderStorage,
): PendingOrder {
  if (state !== 'unknown' && state !== 'submitting') throw new Error('待确认订单状态不正确。')
  const current = readPendingOrder(scope, storage)
  if (current === null) throw new PendingOrderStorageError('待确认订单记录不存在，无法更新状态。')
  const pending: PendingOrder = { ...current, items: current.items.map((item) => ({ ...item })), state }
  const store = storage ?? defaultStorage()
  try {
    store.setItem(scopeStorageKey(scope), JSON.stringify(pending))
  } catch (error) {
    throw new PendingOrderStorageError('更新待确认订单状态失败，请检查浏览器存储空间后重试。', error)
  }
  return pending
}

export function clearPendingOrder(scope: PendingOrderScope, storage?: PendingOrderStorage): void {
  const key = scopeStorageKey(scope)
  const store = storage ?? defaultStorage()
  try {
    store.removeItem(key)
  } catch (error) {
    throw new PendingOrderStorageError('清除待确认订单失败，请检查浏览器存储设置。', error)
  }
}
