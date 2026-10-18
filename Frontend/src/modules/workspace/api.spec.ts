import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parseApiJson } from '@/shared/http/precision'
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
vi.mock('@/shared/http/client', () => ({ http }))
import * as api from './api'

const huge = '9007199254740997'
const application = parseApiJson(`{"id":${huge},"shop_id":${huge},"merchant_id":${huge},"applicant_user_id":${huge},"reviewer_user_id":null,"created_shop_id":null,"status":"PENDING"}`)
describe('workspace real API contracts', () => {
  beforeEach(() => { vi.clearAllMocks(); http.post.mockResolvedValue({ data: application }) })
  it('uses exact JSON numeric BIGINT ids for shop and employee applications and normalizes responses', async () => {
    expect((await api.applyShop(huge, '茶店')).merchant_id).toBe(huge)
    expect((await api.applyEmployee(huge)).shop_id).toBe(huge)
    expect(http.post.mock.calls.map(([url, body, config]) => [url, config.transformRequest[0](body)])).toEqual([
      ['/shops/applications', `{"merchant_id":${huge},"shop_name":"茶店"}`],
      ['/employees/applications', `{"shop_id":${huge}}`],
    ])
  })
  it('sends strictly empty warehouse request and handles bodyless employee removal', async () => {
    expect((await api.applyWarehouse()).id).toBe(huge)
    expect(http.post).toHaveBeenCalledWith('/inventory/access-applications', {}, api.quiet)
    http.delete.mockResolvedValue({ status: 204 })
    await expect(api.removeEmployee(huge, huge)).resolves.toBeUndefined()
  })
  it('assigns an exact BIGINT role id and retains the real returned relationship', async () => {
    http.patch.mockResolvedValue({ data: { id: huge, shop_id: huge, user_id: huge, role_id: huge, role_name: '销售' } })
    expect((await api.assignRole(huge, huge, huge)).role_id).toBe(huge)
    const [url, body, config] = http.patch.mock.calls[0]
    expect(url).toBe(`/shops/${huge}/employees/${huge}/role`)
    expect(config.transformRequest[0](body)).toBe(`{"role_id":${huge}}`)
  })
  it('uses only the existing PUT owner contract, exact target ids and trimmed FORCE reason', async () => {
    http.put.mockResolvedValue({ data: { shop_id: huge, owner_user_id: huge, target_user_id: null, proposal_id: null, status: 'FORCED' } })
    await api.transferOwner(huge, { action: 'FORCE', target_user_id: huge, reason: '  异常处理  ' })
    const [url, body, config] = http.put.mock.calls[0]
    expect(url).toBe(`/shops/${huge}/owner`)
    expect(config.transformRequest[0](body)).toBe(`{"action":"FORCE","target_user_id":${huge},"reason":"异常处理"}`)
    await api.transferOwner(huge, { action: 'CONFIRM', proposal_id: 'exact-uuid' })
    expect(http.put.mock.calls[1][1]).toEqual({ action: 'CONFIRM', proposal_id: 'exact-uuid' })
  })
  it('whitelists review fields and rejects invalid positive BIGINT or blank text', async () => {
    await api.reviewEmployee(huge, 'APPROVE', 'must not send')
    expect(http.post.mock.calls[0][1]).toEqual({ decision: 'APPROVE' })
    expect(api.validId('9223372036854775808')).toBe(false)
    expect(api.validId('1e3')).toBe(false)
    expect(api.validId(huge)).toBe(true)
    expect(api.validText('  ', 100)).toBe(false)
  })
})
