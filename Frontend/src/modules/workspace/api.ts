import { http } from '@/shared/http/client'
import { normalizeId } from '@/modules/auth/api'
import { stringifyApiJson } from '@/shared/http/precision'
import { normalizeShopApplication, normalizeWarehouseApplication } from '@/modules/merchant/api'
import type { ApplicationStatus, Page } from '@/shared/types/api'

export interface MerchantApplication {
  id: string; applicant_user_id: string; merchant_name: string; status: ApplicationStatus
  reviewer_user_id: string | null; rejection_reason: string | null; created_merchant_id: string | null
  created_at: string; reviewed_at: string | null
}
export interface EmployeeApplication {
  id: string; shop_id: string; applicant_user_id: string; status: ApplicationStatus
  reviewer_user_id: string | null; rejection_reason: string | null; created_at: string; reviewed_at: string | null
}
export interface Employee { id: string; shop_id: string; user_id: string; role_id: string; role_name: string; joined_at: string }
export interface EmployeeRole { id: string; shop_id: string; name: string; created_at: string }
export interface DirectoryMerchant { id: string; name: string }
export type TransferRequest = { action: 'PROPOSE'; target_user_id: string } | { action: 'CONFIRM'; proposal_id: string } | { action: 'FORCE'; target_user_id: string; reason: string }
export interface TransferResult { shop_id: string; owner_user_id: string; status: 'PROPOSED' | 'CONFIRMED' | 'FORCED'; proposal_id: string | null; target_user_id: string | null }
export const quiet = { _suppressGlobalError: true }
const exact = { ...quiet, transformRequest: [stringifyApiJson] }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object') throw new Error('服务端数据格式不正确。')
  return value as Record<string, unknown>
}
function ids<T>(value: unknown, fields: string[]): T {
  const row = { ...record(value) }
  for (const field of fields) row[field] = row[field] === null ? null : normalizeId(row[field], field)
  return row as T
}
function page<T>(value: unknown, fields: string[]): Page<T> {
  const row = record(value)
  if (!Array.isArray(row.items) || !Number.isSafeInteger(row.limit) || !Number.isSafeInteger(row.offset) || !Number.isSafeInteger(row.total)) throw new Error('服务端分页格式不正确。')
  return { items: row.items.map(item => ids<T>(item, fields)), limit: row.limit as number, offset: row.offset as number, total: row.total as number }
}
const appIds = ['id', 'applicant_user_id', 'reviewer_user_id']
export async function directory(limit = 20, offset = 0) { return page<DirectoryMerchant>((await http.get('/merchants', { ...quiet, params: { limit, offset } })).data, ['id']) }
export async function merchantApplications(limit = 20, offset = 0, status?: string) { return page<MerchantApplication>((await http.get('/merchants/applications', { ...quiet, params: { limit, offset, status } })).data, [...appIds, 'created_merchant_id']) }
export async function applyMerchant(name: string) { return ids<MerchantApplication>((await http.post('/merchants/applications', { merchant_name: name }, quiet)).data, [...appIds, 'created_merchant_id']) }
export async function applyShop(merchantId: string, name: string) { return normalizeShopApplication((await http.post('/shops/applications', { merchant_id: BigInt(merchantId), shop_name: name }, exact)).data) }
export async function employeeApplications(limit = 20, offset = 0, status?: string) { return page<EmployeeApplication>((await http.get('/employees/applications', { ...quiet, params: { limit, offset, status } })).data, [...appIds, 'shop_id']) }
export async function applyEmployee(shopId: string) { return ids<EmployeeApplication>((await http.post('/employees/applications', { shop_id: BigInt(shopId) }, exact)).data, [...appIds, 'shop_id']) }
export async function reviewEmployee(id: string, decision: 'APPROVE' | 'REJECT', reason?: string) { return ids<EmployeeApplication>((await http.post(`/employees/applications/${id}/review`, decision === 'APPROVE' ? { decision } : { decision, rejection_reason: reason }, quiet)).data, [...appIds, 'shop_id']) }
export async function employees(shopId: string, limit = 20, offset = 0) { return page<Employee>((await http.get(`/shops/${shopId}/employees`, { ...quiet, params: { limit, offset } })).data, ['id', 'shop_id', 'user_id', 'role_id']) }
export async function roles(shopId: string, limit = 20, offset = 0) { return page<EmployeeRole>((await http.get(`/shops/${shopId}/employee-roles`, { ...quiet, params: { limit, offset } })).data, ['id', 'shop_id']) }
export async function createRole(shopId: string, name: string) { return ids<EmployeeRole>((await http.post(`/shops/${shopId}/employee-roles`, { name }, quiet)).data, ['id', 'shop_id']) }
export async function assignRole(shopId: string, userId: string, roleId: string) { return ids<Employee>((await http.patch(`/shops/${shopId}/employees/${userId}/role`, { role_id: BigInt(roleId) }, exact)).data, ['id', 'shop_id', 'user_id', 'role_id']) }
export async function removeEmployee(shopId: string, userId: string): Promise<void> { await http.delete(`/shops/${shopId}/employees/${userId}`, quiet) }
export async function applyWarehouse() { return normalizeWarehouseApplication((await http.post('/inventory/access-applications', {}, quiet)).data) }
export async function transferOwner(shopId: string, request: TransferRequest): Promise<TransferResult> {
  const body = request.action === 'CONFIRM' ? { action: request.action, proposal_id: request.proposal_id } : request.action === 'PROPOSE' ? { action: request.action, target_user_id: BigInt(request.target_user_id) } : { action: request.action, target_user_id: BigInt(request.target_user_id), reason: request.reason.trim() }
  return ids<TransferResult>((await http.put(`/shops/${shopId}/owner`, body, exact)).data, ['shop_id', 'owner_user_id', 'target_user_id'])
}
export function validId(value: string) { return /^[1-9]\d*$/.test(value) && BigInt(value) <= 9223372036854775807n }
export function validText(value: string, max: number) { return value.trim().length > 0 && [...value.trim()].length <= max }
export function statusName(status: string) { return ({ PENDING: '待审核', APPROVED: '已批准', REJECTED: '已驳回' } as Record<string, string>)[status] ?? status }
