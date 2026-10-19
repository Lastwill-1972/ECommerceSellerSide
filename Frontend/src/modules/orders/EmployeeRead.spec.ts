import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { Component } from 'vue'
import { ElButton } from '@/modules/merchant/test-support/elementPlus'
import { useAuthStore } from '@/modules/auth/store'
import ShopReadPage from '@/modules/workspace/pages/ShopReadPage.vue'
import EmployeeHomePage from './EmployeeHomePage.vue'
import { ApiError } from '@/shared/http/errors'

const m = vi.hoisted(() => ({
  getWarehouse: vi.fn(),
  listStocks: vi.fn(),
  listShops: vi.fn(),
  listWarehouseApplications: vi.fn(),
  listCategories: vi.fn(),
  listSpus: vi.fn(),
  getSpu: vi.fn(),
}))

vi.mock('@/modules/merchant/api', () => ({
  listShops: m.listShops,
  listWarehouseApplications: m.listWarehouseApplications,
}))
vi.mock('@/modules/merchant/inventoryApi', () => ({
  getWarehouse: m.getWarehouse,
  listStocks: m.listStocks,
}))
vi.mock('@/modules/merchant/catalogApi', () => ({
  listCategories: m.listCategories,
  listSpus: m.listSpus,
  getSpu: m.getSpu,
}))
vi.mock('element-plus', async () => (await import('@/modules/merchant/test-support/elementPlus')).elementPlusMock)

const placeholder = { template: '<div />' }
let wrapper: VueWrapper | undefined

// 登记模板中实际渲染的 RouterLink 目标，避免 memory router 对未登记路径报警。
const readRoutes = [
  { path: '/', name: 'dashboard', component: placeholder },
  { path: '/employee', name: 'employee-home', component: EmployeeHomePage },
  { path: '/employee/inventory', name: 'employee-inventory', component: ShopReadPage },
  { path: '/employee/catalog', name: 'employee-catalog', component: placeholder },
  { path: '/employee/orders', name: 'employee-orders', component: placeholder },
  { path: '/employee/orders/create', name: 'employee-order-create', component: placeholder },
  { path: '/shop/warehouse', name: 'shop-warehouse', component: ShopReadPage },
]

async function mountAt(component: Component, path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: readRoutes })
  await router.push(path)
  await router.isReady()
  wrapper = mount(component, {
    global: { plugins: [router], stubs: { ElButton } },
  })
  await flushPromises()
  return router
}

function seedAuth() {
  const auth = useAuthStore()
  auth.token = 'tok'
  auth.identityState = 'ready'
  auth.user = {
    id: '5',
    username: 'employee',
    email: 'e@example.com',
    role: 'SHOP_EMPLOYEE',
    merchant_id: '10',
    shop_id: '20',
  }
  return auth
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  seedAuth()
  m.listShops.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 })
  m.listWarehouseApplications.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 })
  m.listCategories.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 })
  m.listSpus.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 })
  m.getSpu.mockResolvedValue(null)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

describe('EmployeeRead', () => {
  it('case1: 403 拒绝时保持角色与路由，显示审批/联系文案，无写操作入口', async () => {
    m.getWarehouse.mockRejectedValue(new ApiError('未授权', 403))
    m.listStocks.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 })

    const router = await mountAt(ShopReadPage, '/employee/inventory')
    const auth = useAuthStore()

    expect(router.currentRoute.value.name).toBe('employee-inventory')
    expect(auth.user?.role).toBe('SHOP_EMPLOYEE')
    const text = wrapper!.text()
    expect(text).toContain('商家管理员审批')
    expect(text).toContain('联系本店负责人')
    expect(wrapper!.find("a[href='/shop/warehouse']").exists()).toBe(false)
    const buttons = wrapper!.findAll('button')
    for (const b of buttons) {
      const t = b.text()
      expect(t).not.toContain('申请仓库')
      expect(t).not.toContain('人工库存')
    }
  })

  it('case2: 授权仓库仅表格只读，无 form 或人工调整按钮', async () => {
    m.getWarehouse.mockResolvedValue({ id: '40', merchant_id: '10', name: '共享仓库' })
    m.listStocks.mockResolvedValue({
      items: [
        {
          sku_id: '8',
          warehouse_id: '40',
          sku_code: 'SKU',
          quantity: '10',
          spu_name: '茶',
          specification: '标准',
        },
      ],
      limit: 20,
      offset: 0,
      total: 1,
    })

    await mountAt(ShopReadPage, '/employee/inventory')

    expect(wrapper!.find('table').exists()).toBe(true)
    expect(wrapper!.text()).toContain('SKU')
    expect(wrapper!.find('form').exists()).toBe(false)
    const buttons = wrapper!.findAll('button')
    for (const b of buttons) {
      expect(b.text()).not.toContain('人工调整')
    }
  })

  it('case3: EmployeeHome 不调用 shops/warehouse，刷新身份可重复点击且调用两次', async () => {
    const auth = useAuthStore()
    const refreshSpy = vi
      .spyOn(auth, 'refreshIdentity')
      .mockImplementation(async () => {
        auth.scopeEpoch++
        auth.user = null
        await Promise.resolve()
        auth.user = {
          id: '5',
          username: 'employee',
          email: 'e@example.com',
          role: 'SHOP_EMPLOYEE',
          merchant_id: '10',
          shop_id: '20',
        }
        return auth.user
      })

    await mountAt(EmployeeHomePage, '/employee')

    expect(wrapper!.text()).toContain('5')
    expect(m.listShops).not.toHaveBeenCalled()
    expect(m.getWarehouse).not.toHaveBeenCalled()
    expect(m.listStocks).not.toHaveBeenCalled()
    expect(m.listWarehouseApplications).not.toHaveBeenCalled()

    const btn = wrapper!.findAll('button').find((b) => b.text().includes('刷新身份'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()
    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect((btn!.element as HTMLButtonElement).disabled).toBe(false)

    await btn!.trigger('click')
    await flushPromises()
    expect(refreshSpy).toHaveBeenCalledTimes(2)
    expect((btn!.element as HTMLButtonElement).disabled).toBe(false)
    expect(auth.user?.role).toBe('SHOP_EMPLOYEE')
  })
})
