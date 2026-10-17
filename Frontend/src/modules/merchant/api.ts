import { http } from '@/shared/http/client'
import { normalizeId } from '@/modules/auth/api'
import type { ApplicationDecision, MerchantProfile, Order, Page, Shop, ShopApplication, WarehouseAccessApplication } from '@/shared/types/api'

const noToast = { _suppressGlobalError: true }
function entity<T extends Record<string, unknown>>(data: T, idFields: (keyof T)[]): T {
  const result = { ...data }
  for (const field of idFields) {
    const value = result[field]
    if (value !== null && value !== undefined) result[field] = normalizeId(value, String(field)) as T[keyof T]
  }
  return result
}
function page<T>(data: unknown, normalize: (value: unknown) => T): Page<T> {
  if (typeof data !== 'object' || data === null) throw new Error('服务器返回的列表格式不正确。')
  const raw = data as Record<string, unknown>
  if (!Array.isArray(raw.items) || !Number.isSafeInteger(raw.limit) || !Number.isSafeInteger(raw.offset) || !Number.isSafeInteger(raw.total)) {
    throw new Error('服务器返回的分页信息格式不正确。')
  }
  return { items: raw.items.map(normalize), limit: raw.limit as number, offset: raw.offset as number, total: raw.total as number }
}
export function normalizeShop(value: unknown): Shop {
  if (typeof value !== 'object' || value === null) throw new Error('店铺数据格式不正确。')
  const item = value as Record<string, unknown>
  return entity(item, ['id', 'merchant_id', 'owner_user_id']) as unknown as Shop
}
export function normalizeShopApplication(value: unknown): ShopApplication {
  if (typeof value !== 'object' || value === null) throw new Error('店铺申请数据格式不正确。')
  const item = value as Record<string, unknown>
  return entity(item, ['id', 'merchant_id', 'applicant_user_id', 'reviewer_user_id', 'created_shop_id']) as unknown as ShopApplication
}
export function normalizeWarehouseApplication(value: unknown): WarehouseAccessApplication {
  if (typeof value !== 'object' || value === null) throw new Error('仓库授权申请数据格式不正确。')
  const item = value as Record<string, unknown>
  return entity(item, ['id', 'shop_id', 'applicant_user_id', 'reviewer_user_id']) as unknown as WarehouseAccessApplication
}
export function normalizeOrder(value: unknown): Order {
  if (typeof value !== 'object' || value === null) throw new Error('订单数据格式不正确。')
  const item = value as Record<string, unknown>
  const result = entity(item, ['id', 'merchant_id', 'shop_id', 'created_by_user_id']) as Record<string, unknown>
  if (!Array.isArray(item.items)) throw new Error('订单明细数据格式不正确。')
  result.items = item.items.map((raw) => {
    if (typeof raw !== 'object' || raw === null) throw new Error('订单明细数据格式不正确。')
    const line = raw as Record<string, unknown>
    const normalized = entity(line, ['id', 'sku_id', 'quantity'])
    for (const field of ['unit_price', 'subtotal']) if (typeof normalized[field] !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(normalized[field] as string)) throw new Error(`订单${field}格式不正确。`)
    return normalized
  })
  if (typeof result.total_amount !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(result.total_amount)) throw new Error('订单金额格式不正确。')
  return result as unknown as Order
}

export async function updateMerchantName(name: string): Promise<MerchantProfile> {
  const { data } = await http.patch('/merchants/me', { name }, noToast)
  if (typeof data !== 'object' || data === null) throw new Error('商家资料格式不正确。')
  const result = entity(data as Record<string, unknown>, ['id'])
  if (typeof result.name !== 'string') throw new Error('商家资料格式不正确。')
  return result as unknown as MerchantProfile
}
export async function listShops(limit: number, offset: number): Promise<Page<Shop>> {
  const { data } = await http.get('/shops', { ...noToast, params: { limit, offset } })
  return page(data, normalizeShop)
}
export async function listShopApplications(limit: number, offset: number, status?: string): Promise<Page<ShopApplication>> {
  const { data } = await http.get('/shops/applications', { ...noToast, params: { limit, offset, ...(status ? { status } : {}) } })
  return page(data, normalizeShopApplication)
}
export async function reviewShopApplication(id: string, decision: ApplicationDecision, rejection_reason?: string): Promise<ShopApplication> {
  const body = decision === 'APPROVE' ? { decision } : { decision, rejection_reason }
  const { data } = await http.post(`/shops/applications/${id}/review`, body, noToast)
  return normalizeShopApplication(data)
}
export async function listWarehouseApplications(limit: number, offset: number, status?: string): Promise<Page<WarehouseAccessApplication>> {
  const { data } = await http.get('/inventory/access-applications', { ...noToast, params: { limit, offset, ...(status ? { status } : {}) } })
  return page(data, normalizeWarehouseApplication)
}
export async function reviewWarehouseApplication(id: string, decision: ApplicationDecision, rejection_reason?: string): Promise<WarehouseAccessApplication> {
  const body = decision === 'APPROVE' ? { decision } : { decision, rejection_reason }
  const { data } = await http.post(`/inventory/access-applications/${id}/review`, body, noToast)
  return normalizeWarehouseApplication(data)
}
export async function listOrders(limit: number, offset: number, filters: { status?: string; shop_id?: string }): Promise<Page<Order>> {
  const { data } = await http.get('/orders', { ...noToast, params: { limit, offset, ...filters } })
  return page(data, normalizeOrder)
}
export async function getOrder(id: string): Promise<Order> {
  const { data } = await http.get(`/orders/${id}`, noToast)
  return normalizeOrder(data)
}

