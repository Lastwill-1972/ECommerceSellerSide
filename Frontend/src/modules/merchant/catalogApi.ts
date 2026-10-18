import { http } from '@/shared/http/client'
import { normalizeId } from '@/modules/auth/api'
import { stringifyApiJson } from '@/shared/http/precision'
import type { Category, OffshelfApplication, Page, Spu, SpuDetail, Sku } from '@/shared/types/api'

const noToast = { _suppressGlobalError: true }
const exactConfig = { ...noToast, transformRequest: [stringifyApiJson], headers: { 'Content-Type': 'application/json' } }
function record(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) throw new Error(message)
  return value as Record<string, unknown>
}
function ids<T extends Record<string, unknown>>(value: T, fields: (keyof T)[]): T {
  const result = { ...value }
  for (const field of fields) if (result[field] !== null && result[field] !== undefined) result[field] = normalizeId(result[field], String(field)) as T[keyof T]
  return result
}
function page<T>(value: unknown, parse: (row: unknown) => T): Page<T> {
  const raw = record(value, '服务器返回的列表格式不正确。')
  if (!Array.isArray(raw.items) || !Number.isSafeInteger(raw.limit) || !Number.isSafeInteger(raw.offset) || !Number.isSafeInteger(raw.total)) throw new Error('服务器返回的分页信息格式不正确。')
  return { items: raw.items.map(parse), limit: raw.limit as number, offset: raw.offset as number, total: raw.total as number }
}
function parseCategory(value: unknown): Category { return ids(record(value, '分类数据格式不正确。'), ['id', 'merchant_id']) as unknown as Category }
function parseSpu(value: unknown): Spu { return ids(record(value, '商品数据格式不正确。'), ['id', 'merchant_id', 'category_id']) as unknown as Spu }
function parseSku(value: unknown): Sku {
  const raw = ids(record(value, 'SKU 数据格式不正确。'), ['id', 'spu_id'])
  if (typeof raw.price !== 'string' || !/^\d+(?:\.\d{1,2})?$/.test(raw.price)) throw new Error('SKU 价格格式不正确。')
  return raw as unknown as Sku
}
function parseOffshelf(value: unknown): OffshelfApplication {
  const raw = record(value, '下架申请数据格式不正确。')
  const result = ids(raw, ['id', 'spu_id', 'applicant_user_id', 'reviewer_user_id'])
  result.spu = ids(record(raw.spu, '申请商品数据格式不正确。'), ['id', 'merchant_id'])
  return result as unknown as OffshelfApplication
}

export async function listCategories(limit: number, offset: number) {
  const { data } = await http.get('/catalog/categories', { ...noToast, params: { limit, offset } })
  return page(data, parseCategory)
}
export async function createCategory(name: string) {
  const { data } = await http.post('/catalog/categories', { name }, noToast)
  return parseCategory(data)
}
export async function listSpus(limit: number, offset: number, filters: { category_id?: string; status?: string } = {}) {
  const { data } = await http.get('/catalog/spus', { ...noToast, params: { limit, offset, ...filters } })
  return page(data, parseSpu)
}
export async function createSpu(payload: { category_id: string; name: string; description: string | null }) {
  const { data } = await http.post('/catalog/spus', { category_id: BigInt(payload.category_id), name: payload.name, description: payload.description }, exactConfig)
  return parseSpu(data)
}
export async function getSpu(id: string): Promise<SpuDetail> {
  const { data } = await http.get(`/catalog/spus/${id}`, noToast)
  const raw = record(data, '商品详情格式不正确。')
  if (!Array.isArray(raw.skus)) throw new Error('商品 SKU 数据格式不正确。')
  return { ...parseSpu(raw), skus: raw.skus.map(parseSku) }
}
export async function updateSpu(id: string, payload: { category_id?: string; name?: string; description?: string | null }) {
  const body = {
    ...(payload.category_id !== undefined ? { category_id: BigInt(payload.category_id) } : {}),
    ...(payload.name !== undefined ? { name: payload.name } : {}),
    ...(payload.description !== undefined ? { description: payload.description } : {}),
  }
  const { data } = await http.patch(`/catalog/spus/${id}`, body, exactConfig)
  return parseSpu(data)
}
export async function publishSpu(id: string) {
  const { data } = await http.patch(`/catalog/spus/${id}/publish`, undefined, noToast)
  return parseSpu(data)
}
export async function createSku(spuId: string, payload: { sku_code: string; specification: string; price: string }) {
  const body = { sku_code: payload.sku_code, specification: payload.specification, price: payload.price }
  const { data } = await http.post(`/catalog/spus/${spuId}/skus`, body, noToast)
  return parseSku(data)
}
export async function updateSku(id: string, payload: { sku_code?: string; specification?: string; price?: string }) {
  const body = {
    ...(payload.sku_code !== undefined ? { sku_code: payload.sku_code } : {}),
    ...(payload.specification !== undefined ? { specification: payload.specification } : {}),
    ...(payload.price !== undefined ? { price: payload.price } : {}),
  }
  const { data } = await http.patch(`/catalog/skus/${id}`, body, noToast)
  return parseSku(data)
}
export async function listOffshelfApplications(limit: number, offset: number, status?: string) {
  const { data } = await http.get('/catalog/offshelf-applications', { ...noToast, params: { limit, offset, ...(status ? { status } : {}) } })
  return page(data, parseOffshelf)
}
export async function submitOffshelfApplication(spuId: string) {
  const { data } = await http.post('/catalog/offshelf-applications', { spu_id: BigInt(spuId) }, exactConfig)
  return parseOffshelf(data)
}
