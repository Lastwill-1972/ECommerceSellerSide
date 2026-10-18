import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/shared/http/errors'
import { ElMessage } from 'element-plus'
import ButtonStub from '@/modules/merchant/test-support/ButtonStub'
import InputStub from '@/modules/merchant/test-support/InputStub'
import DialogStub from '@/modules/merchant/test-support/DialogStub'
import SelectStub from '@/modules/merchant/test-support/SelectStub'
import OptionStub from '@/modules/merchant/test-support/OptionStub'
import AlertStub from '@/modules/merchant/test-support/AlertStub'
import ReviewQueue from './ReviewQueue.vue'
import { listShopApplications, listShops, listWarehouseApplications, reviewShopApplication, reviewWarehouseApplication } from '@/modules/merchant/api'

vi.mock('@/modules/merchant/api', () => ({
  listShopApplications: vi.fn(), listShops: vi.fn(), listWarehouseApplications: vi.fn(),
  reviewShopApplication: vi.fn(), reviewWarehouseApplication: vi.fn(),
}))
vi.mock('element-plus', async (importOriginal) => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: { success: vi.fn(), error: vi.fn() },
}))

const page = (items: unknown[]) => ({ items, limit: 20, offset: 0, total: items.length })
const shopApp = { id: '9007199254740999', merchant_id: '9007199254740993', applicant_user_id: '9007199254740995', shop_name: '新店', status: 'PENDING', reviewer_user_id: null, rejection_reason: null, created_shop_id: null, created_at: '2026-10-17T09:30:00', reviewed_at: null }
const warehouseApp = { id: '9007199254741001', shop_id: '9007199254740997', applicant_user_id: '9007199254740995', status: 'PENDING', reviewer_user_id: null, rejection_reason: null, created_at: '2026-10-17T09:30:00', reviewed_at: null }
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
const sampleShop = { id: '9007199254740997', merchant_id: '9007199254740993', name: '示例店', owner_user_id: '9007199254740999', created_at: '2026-10-17T09:00:00' }

async function mountQueue(kind: 'shop' | 'warehouse', rows?: unknown[]) {
  setActivePinia(createPinia())
  const { useAuthStore } = await import('@/modules/auth/store')
  const auth = useAuthStore()
  auth.token = 'test-token'
  auth.user = { id: '9007199254740995', username: 'admin', email: 'a@b.test', role: 'MERCHANT_ADMIN', merchant_id: '9007199254740993', shop_id: null }
  auth.identityState = 'ready'
  const shopRows = rows ?? [shopApp]
  vi.mocked(listShopApplications).mockImplementation(async (_limit, offset) => ({ ...page(offset === 0 ? shopRows : []), offset, total: shopRows.length > 20 ? 21 : shopRows.length }) as never)
  vi.mocked(listWarehouseApplications).mockResolvedValue(page(rows ?? [warehouseApp]) as never)
  vi.mocked(listShops).mockResolvedValue(page([sampleShop]) as never)
  vi.mocked(reviewShopApplication).mockResolvedValue(shopApp as never)
  vi.mocked(reviewWarehouseApplication).mockResolvedValue(warehouseApp as never)
  const wrapper = mount(ReviewQueue, { props: { kind }, global: { plugins: [router], stubs: { ElButton: ButtonStub, ElInput: InputStub, ElDialog: DialogStub, ElSelect: SelectStub, ElOption: OptionStub, ElAlert: AlertStub } } })
  await flushPromises()
  return wrapper
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => { document.body.innerHTML = '' })

describe('merchant review queue', () => {
  it('approves a shop application once and omits rejection_reason', async () => {
    const wrapper = await mountQueue('shop')
    await wrapper.findAll('button').find((node) => node.text() === '批准')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find((node) => node.text().includes('确认批准'))!.trigger('click')
    await flushPromises()
    expect(reviewShopApplication).toHaveBeenCalledTimes(1)
    expect(reviewShopApplication).toHaveBeenCalledWith(shopApp.id, 'APPROVE', undefined)
    wrapper.unmount()
  })

  it('requires a trimmed rejection reason of at most 2000 Unicode code points', async () => {
    const wrapper = await mountQueue('shop')
    await wrapper.findAll('button').find((node) => node.text() === '拒绝')!.trigger('click')
    const textarea = wrapper.get('textarea')
    await textarea.setValue(`  ${'😀'.repeat(2000)}  `)
    await flushPromises()
    const submit = wrapper.findAll('button').find((node) => node.text().includes('确认拒绝'))!
    expect(submit.attributes('disabled')).toBeUndefined()
    await textarea.setValue('😀'.repeat(2001))
    await flushPromises()
    expect(submit.attributes('disabled')).toBeDefined()
    await textarea.setValue(`  ${'😀'.repeat(2000)}  `)
    await flushPromises()
    await submit.trigger('click'); await flushPromises()
    expect(reviewShopApplication).toHaveBeenCalledWith(shopApp.id, 'REJECT', '😀'.repeat(2000))
    wrapper.unmount()
  })

  it('shows the shared warehouse warning and sends an approval without reason', async () => {
    const wrapper = await mountQueue('warehouse')
    expect(wrapper.text()).toContain('示例店')
    await wrapper.findAll('button').find((node) => node.text() === '批准')!.trigger('click')
    expect(wrapper.text()).toContain('批准代表店铺获得共享仓库使用资格，不等于任意库存修改权。')
    await wrapper.findAll('button').find((node) => node.text().includes('确认批准'))!.trigger('click')
    await flushPromises()
    expect(reviewWarehouseApplication).toHaveBeenCalledTimes(1)
    expect(reviewWarehouseApplication).toHaveBeenCalledWith(warehouseApp.id, 'APPROVE', undefined)
    wrapper.unmount()
  })

  it('blocks repeat submission while pending and refreshes without retrying a conflict', async () => {
    const wrapper = await mountQueue('shop')
    let finish!: () => void
    vi.mocked(reviewShopApplication).mockReturnValue(new Promise((resolve) => { finish = () => resolve(shopApp as never) }))
    await wrapper.findAll('button').find((node) => node.text() === '批准')!.trigger('click')
    const approve = wrapper.findAll('button').find((node) => node.text().includes('确认批准'))!
    await approve.trigger('click')
    await approve.trigger('click')
    expect(reviewShopApplication).toHaveBeenCalledTimes(1)
    finish(); await flushPromises()
    wrapper.unmount()

    const conflictWrapper = await mountQueue('shop')
    vi.mocked(reviewShopApplication).mockRejectedValueOnce(new ApiError('conflict', 409, {}, 'conflict'))
    await conflictWrapper.findAll('button').find((node) => node.text() === '批准')!.trigger('click')
    await conflictWrapper.findAll('button').find((node) => node.text().includes('确认批准'))!.trigger('click')
    await flushPromises()
    expect(reviewShopApplication).toHaveBeenCalledTimes(2)
    expect(listShopApplications).toHaveBeenCalledTimes(4)
    expect(ElMessage.error).toHaveBeenCalled()
    conflictWrapper.unmount()
  })

  it('keeps row actions locked until the prior dialog close transition finishes', async () => {
    const secondApplication = { ...shopApp, id: '9007199254741101', shop_name: '第二家店' }
    const wrapper = await mountQueue('shop', [shopApp, secondApplication])
    await wrapper.findAll('button').find((node) => node.text() === '批准')!.trigger('click')
    await wrapper.findAll('button').find((node) => node.text().includes('确认批准'))!.trigger('click')
    await flushPromises()
    const rejectButtons = wrapper.findAll('button').filter((node) => node.text() === '拒绝')
    expect(rejectButtons).toHaveLength(2)
    expect(rejectButtons[1].attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('textarea')).toHaveLength(0)
    await new Promise((resolve) => setTimeout(resolve, 30))
    await rejectButtons[1].trigger('click')
    expect(wrapper.findAll('textarea')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows an empty state and requests the next page with an offset', async () => {
    const empty = await mountQueue('shop', [])
    expect(empty.text()).toContain('没有符合筛选条件的申请。')
    empty.unmount()

    const rows = Array.from({ length: 21 }, (_, index) => ({ ...shopApp, id: String(9007199254741000 + index), shop_name: `申请 ${index}` }))
    const wrapper = await mountQueue('shop', rows)
    await wrapper.findAll('button').find((node) => node.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(listShopApplications).toHaveBeenLastCalledWith(20, 20, undefined)
    expect(wrapper.text()).toContain('第 2 页')
    wrapper.unmount()
  })
})
