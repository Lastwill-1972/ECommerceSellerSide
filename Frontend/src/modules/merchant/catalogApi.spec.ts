import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parseApiJson } from '@/shared/http/precision'

const mocks = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/shared/http/client', () => ({ http: mocks }))

import { createCategory, createSku, createSpu, listCategories, listOffshelfApplications, publishSpu, submitOffshelfApplication, updateSpu, updateSku } from './catalogApi'

const categoryPageJson = `{"items":[{"id":9007199254740993,"merchant_id":9007199254740995,"name":"饮品","created_at":"2026-10-01T10:00:00"}],"limit":100,"offset":200,"total":201}`
const category = { id: '9007199254740993', merchant_id: '9007199254740995', name: '饮品', created_at: '2026-10-01T10:00:00' }

describe('catalog API adapters', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('creates categories and preserves exact category ids and pagination values', async () => {
    mocks.post.mockResolvedValueOnce({ data: category })
    await expect(createCategory('饮品')).resolves.toEqual(category)
    expect(mocks.post).toHaveBeenCalledWith('/catalog/categories', { name: '饮品' }, expect.objectContaining({ _suppressGlobalError: true }))

    mocks.get.mockResolvedValueOnce({ data: parseApiJson(categoryPageJson) })
    const page = await listCategories(100, 200)
    expect(mocks.get).toHaveBeenCalledWith('/catalog/categories', expect.objectContaining({ params: { limit: 100, offset: 200 } }))
    expect(page).toEqual({ items: [category], limit: 100, offset: 200, total: 201 })
  })

  it('keeps a new SPU off sale and serializes large category ids as exact JSON integers', async () => {
    mocks.post.mockResolvedValueOnce({ data: {
      id: '9007199254740997', merchant_id: '9007199254740995', category_id: '9007199254740993',
      name: '茶', description: null, status: 'OFF_SALE', created_at: '2026-10-01T10:00:00', updated_at: '2026-10-01T10:00:00',
    } })
    const spu = await createSpu({ category_id: '9007199254740993', name: '茶', description: null })
    expect(spu.status).toBe('OFF_SALE')
    const [url, body, config] = mocks.post.mock.calls[0]
    expect(url).toBe('/catalog/spus')
    expect(config.transformRequest[0](body)).toContain('"category_id":9007199254740993')
    expect(body).not.toHaveProperty('status')
  })

  it('preserves SKU price text and keeps ordinary SPU patches away from status', async () => {
    const sku = { id: '41', spu_id: '9', sku_code: 'TEA-1', specification: '罐装', price: '1234567890.09', created_at: '2026-10-01T10:00:00', updated_at: '2026-10-01T10:00:00' }
    mocks.post.mockResolvedValueOnce({ data: sku })
    await expect(createSku('9', { sku_code: 'TEA-1', specification: '罐装', price: '1234567890.09' })).resolves.toMatchObject({ price: '1234567890.09' })
    expect(mocks.post).toHaveBeenCalledWith('/catalog/spus/9/skus', { sku_code: 'TEA-1', specification: '罐装', price: '1234567890.09' }, expect.any(Object))

    mocks.patch.mockResolvedValueOnce({ data: { ...sku, price: '0.10' } })
    await updateSku('41', { price: '0.10' })
    expect(mocks.patch.mock.calls[0][1]).toEqual({ price: '0.10' })

    mocks.patch.mockResolvedValueOnce({ data: { id: '9', merchant_id: '10', category_id: '4', name: '茶改名', description: null, status: 'ON_SALE', created_at: '', updated_at: '' } })
    await updateSpu('9', { name: '茶改名' })
    expect(mocks.patch).toHaveBeenLastCalledWith('/catalog/spus/9', { name: '茶改名' }, expect.objectContaining({ transformRequest: expect.any(Array) }))
    expect(mocks.patch.mock.calls[1][1]).not.toHaveProperty('status')
  })

  it('uses the publish endpoint and retains all historical offshelf statuses', async () => {
    const spu = { id: '9', merchant_id: '10', category_id: '4', name: '茶', description: null, status: 'ON_SALE', created_at: '', updated_at: '' }
    mocks.patch.mockResolvedValueOnce({ data: spu })
    await publishSpu('9')
    expect(mocks.patch).toHaveBeenCalledWith('/catalog/spus/9/publish', undefined, expect.any(Object))

    const applications = ['PENDING', 'APPROVED', 'REJECTED'].map((status, index) => ({
      id: String(index + 1), spu_id: '9', applicant_user_id: '20', status,
      reviewer_user_id: status === 'PENDING' ? null : '30', rejection_reason: status === 'REJECTED' ? '资料不完整' : null,
      created_at: '2026-10-01T10:00:00', reviewed_at: status === 'PENDING' ? null : '2026-10-02T10:00:00',
      spu: { id: '9', name: '茶', status: 'ON_SALE', merchant_id: '10' },
    }))
    mocks.get.mockResolvedValueOnce({ data: { items: applications, limit: 10, offset: 0, total: 3 } })
    const page = await listOffshelfApplications(10, 0)
    expect(page.items.map((item) => item.status)).toEqual(['PENDING', 'APPROVED', 'REJECTED'])
    expect(page.items[2].rejection_reason).toBe('资料不完整')

    mocks.post.mockResolvedValueOnce({ data: applications[0] })
    await submitOffshelfApplication('9007199254740993')
    const [, body, config] = mocks.post.mock.calls[0]
    expect(config.transformRequest[0](body)).toContain('"spu_id":9007199254740993')
  })
})
