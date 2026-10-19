import { http } from '@/shared/http/client'
import { normalizeId } from '@/modules/auth/api'
import type { Page } from '@/shared/types/api'

/** An audit event row. Tenant ownership is deliberately not asserted from target_id. */
export interface AuditEvent {
  id: string
  actor_user_id: string
  event_type: string
  target_type: string
  target_id: string
  outcome: string
  event_data: Record<string, unknown>
  created_at: string
}

export interface AuditQuery {
  limit?: number
  offset?: number
  event_type?: string
  target_type?: string
  start_at?: string
  end_at?: string
}

export const quiet = { _suppressGlobalError: true }
const idFields = ['id', 'actor_user_id', 'target_id']
const naive = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object') throw new Error('服务端数据格式不正确。')
  return value as Record<string, unknown>
}

function normalizeEvent(value: unknown): AuditEvent {
  const row = { ...record(value) }
  for (const field of idFields) row[field] = normalizeId(row[field], field)
  const data = row.event_data
  row.event_data = data && typeof data === 'object' && !Array.isArray(data) ? (data as Record<string, unknown>) : {}
  return row as unknown as AuditEvent
}

/** Parse the audit page while keeping every BIGINT identifier as exact decimal text. */
export function normalizeEventPage(value: unknown): Page<AuditEvent> {
  const row = record(value)
  if (!Array.isArray(row.items) || !Number.isSafeInteger(row.limit) || !Number.isSafeInteger(row.offset) || !Number.isSafeInteger(row.total)) throw new Error('服务端分页格式不正确。')
  return { items: row.items.map(normalizeEvent), limit: row.limit as number, offset: row.offset as number, total: row.total as number }
}

/** Naive (timezone-free) datetime text; never passes through Date. */
export function validNaiveDateTime(value: string): boolean {
  return naive.test(value)
}

export function validFilterText(value: string): boolean {
  const text = value.trim()
  return text.length > 0 && [...text].length <= 100
}

/** Only trimmed non-empty optional filters are sent; the server remains authoritative. */
export function queryParams(query: AuditQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: query.limit ?? 20, offset: query.offset ?? 0 }
  for (const key of ['event_type', 'target_type', 'start_at', 'end_at'] as const) {
    const value = query[key]
    if (typeof value === 'string' && value.length > 0) params[key] = value
  }
  return params
}

export async function auditEvents(query: AuditQuery): Promise<Page<AuditEvent>> {
  const { data } = await http.get('/audit/events', { ...quiet, params: queryParams(query) })
  return normalizeEventPage(data)
}
