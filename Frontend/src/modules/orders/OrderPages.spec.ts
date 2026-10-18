import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { defineComponent } from 'vue'

const m = vi.hoisted(() => ({
  listOrders: vi.fn(),
  getOrder: vi.fn(),
  completeOrder: vi.fn(),
  cancelOrder: vi.fn(),
  getWarehouse: vi.fn(),
  listStocks: vi.fn(),
}))

vi.mock('@/modules/merchant/api', () => ({
  listOrders: m.listOrders,
  getOrder: m.getOrder,
}))
vi.mock('@/modules/orders/api', () => ({
  completeOrder: m.completeOrder,
  cancelOrder: m.cancelOrder,
}))
vi.mock('@/modules/merchant/inventoryApi', () => ({
  getWarehouse: m.getWarehouse,
  listStocks: m.listStocks,
}))
vi.mock('element-plus', async () => {
  const mod = await import('@/modules/merchant/test-support/elementPlus')
  return mod.elementPlusMock
})

import OrderListPage from './OrderListPage.vue'
import OrderDetailPage from './OrderDetailPage.vue'
import { ElButton, messageBoxConfirm } from '@/modules/merchant/test-support/elementPlus'
import { useAuthStore } from '@/modules/auth/store'
import { ApiError } from '@/shared/http/errors'

const Empty = defineComponent({ render: () => null })

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/shop/orders', name: 'shop-orders', component: Empty },
      { path: '/shop/orders/:id', name: 'shop-order-detail', component: Empty },
      { path: '/employee/orders', name: 'employee-orders', component: Empty },
      { path: '/employee/orders/:id', name: 'employee-order-detail', component: Empty },
      { path: '/orders/create', name: 'order-create', component: Empty },
      { path: '/dashboard', name: 'dashboard', component: Empty },
    ],
  })
}

function baseOrder() {
  return {
    id: '9007199254740997',
    merchant_id: '10',
    shop_id: '20',
    created_by_user_id: '99',
    status: 'PENDING',
    total_amount: '19.90',
    created_at: '2026-10-18T10:00:00',
    completed_at: null,
    cancelled_at: null,
    items: [
      {
        id: '88',
        sku_id: '77',
        quantity: '2',
        unit_price: '9.95',
        subtotal: '19.90',
        spu_name_snapshot: '历史商品',
        sku_code_snapshot: 'OLD-CODE',
        specification_snapshot: '历史规格',
      },
    ],
  }
}

function orderPage(order: ReturnType<typeof baseOrder>) {
  return { items: [order], limit: 20, offset: 0, total: 1 }
}

function stockPage() {
  return {
    items: [
      {
        warehouse_id: '40',
        sku_id: '77',
        quantity: '5',
        sku_code: 'SKU',
        spu_name: '历史商品',
        specification: '标准',
      },
    ],
    limit: 20,
    offset: 0,
    total: 1,
  }
}

function setAuth(role: 'SHOP_OWNER' | 'SHOP_EMPLOYEE', shopId = '20') {
  const auth = useAuthStore()
  auth.token = 'test-token'
  auth.identityState = 'ready'
  auth.user = {
    id: '5',
    username: 'test',
    email: 't@example.com',
    role,
    merchant_id: '10',
    shop_id: shopId,
  }
  return auth
}

function defaultResponses() {
  m.listOrders.mockResolvedValue(orderPage(baseOrder()))
  m.getOrder.mockResolvedValue(baseOrder())
  m.completeOrder.mockResolvedValue(undefined)
  m.cancelOrder.mockResolvedValue(undefined)
  m.getWarehouse.mockResolvedValue({ id: '40', merchant_id: '10', name: '主仓' })
  m.listStocks.mockResolvedValue(stockPage())
  messageBoxConfirm.mockResolvedValue(undefined)
}

let wrappers: Array<{ unmount: () => void }> = []

function track<T extends { unmount: () => void }>(w: T): T {
  wrappers.push(w)
  return w
}

describe('OrderPages', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    defaultResponses()
  })

  afterEach(() => {
    for (const w of wrappers) w.unmount()
    wrappers = []
    vi.clearAllMocks()
    vi.resetAllMocks()
  })

  it.each(['SHOP_OWNER', 'SHOP_EMPLOYEE'] as const)(
    '%s 可看到本店同事的订单列表',
    async (role) => {
      setAuth(role)
      const router = makeRouter()
      await router.push('/shop/orders')
      await router.isReady()
      const w = track(
        mount(OrderListPage, {
          global: { plugins: [router], stubs: { ElButton } },
        }),
      )
      await flushPromises()

      expect(m.listOrders).toHaveBeenCalledWith(20, 0, {})
      const text = w.text()
      expect(text).toContain('9007199254740997')
      expect(text).toContain('19.90')
      expect(w.find(`a[href="/${role === 'SHOP_OWNER' ? 'shop' : 'employee'}/orders/9007199254740997"]`).exists()).toBe(true)
    },
  )

  it('隐藏外店订单整行', async () => {
    setAuth('SHOP_OWNER')
    const order = baseOrder()
    order.shop_id = '21'
    m.listOrders.mockResolvedValue(orderPage(order))
    const router = makeRouter()
    await router.push('/shop/orders')
    await router.isReady()
    const w = track(
      mount(OrderListPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    expect(w.text()).not.toContain('9007199254740997')
  })

  it('详情展示历史 snapshot 原文 19.90', async () => {
    setAuth('SHOP_OWNER')
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    expect(m.getOrder).toHaveBeenCalledWith('9007199254740997')
    const text = w.text()
    expect(text).toContain('19.90')
    expect(text).toContain('历史商品')
    expect(text).toContain('OLD-CODE')
    expect(text).toContain('历史规格')
  })

  it('完成订单：确认后仅调用一次 completeOrder 并重新拉取', async () => {
    setAuth('SHOP_OWNER')
    m.getOrder
      .mockResolvedValueOnce(baseOrder())
      .mockResolvedValueOnce({
        ...baseOrder(),
        status: 'COMPLETED',
        completed_at: '2026-10-18T11:00:00',
      })
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text() === '完成订单')
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()

    expect(messageBoxConfirm).toHaveBeenCalledTimes(1)
    expect(m.completeOrder).toHaveBeenCalledTimes(1)
    expect(m.completeOrder).toHaveBeenCalledWith('9007199254740997')
    expect(m.getOrder).toHaveBeenCalledTimes(2)
    expect(m.listStocks).toHaveBeenCalled()
  })

  it('取消订单：确认后仅调用一次 cancelOrder 并重新拉取', async () => {
    setAuth('SHOP_OWNER')
    m.getOrder
      .mockResolvedValueOnce(baseOrder())
      .mockResolvedValueOnce({
        ...baseOrder(),
        status: 'CANCELLED',
        cancelled_at: '2026-10-18T11:00:00',
      })
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text() === '取消订单')
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()

    expect(messageBoxConfirm).toHaveBeenCalledTimes(1)
    expect(m.cancelOrder).toHaveBeenCalledTimes(1)
    expect(m.cancelOrder).toHaveBeenCalledWith('9007199254740997')
    expect(m.getOrder).toHaveBeenCalledTimes(2)
  })

  it.each(['COMPLETED', 'CANCELLED'] as const)(
    '非 PENDING（%s）不显示操作按钮',
    async (status) => {
      setAuth('SHOP_OWNER')
      m.getOrder.mockResolvedValue({ ...baseOrder(), status })
      const router = makeRouter()
      await router.push('/shop/orders/9007199254740997')
      await router.isReady()
      const w = track(
        mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
      )
      await flushPromises()

      const labels = w.findAll('button').map((b) => b.text())
      expect(labels).not.toContain('完成订单')
      expect(labels).not.toContain('取消订单')
    },
  )

  it('409 状态冲突：仅重新 GET，不发起第二次 POST', async () => {
    setAuth('SHOP_OWNER')
    m.getOrder
      .mockResolvedValueOnce(baseOrder())
      .mockResolvedValueOnce({ ...baseOrder(), status: 'COMPLETED' })
    m.completeOrder.mockRejectedValueOnce(new ApiError('状态冲突', 409))
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text() === '完成订单')
    await btn!.trigger('click')
    await flushPromises()

    expect(m.completeOrder).toHaveBeenCalledTimes(1)
    expect(m.getOrder).toHaveBeenCalledTimes(2)
  })

  it('503 已提交但响应丢失：GET 到终态且不重发', async () => {
    setAuth('SHOP_OWNER')
    m.getOrder
      .mockResolvedValueOnce(baseOrder())
      .mockResolvedValueOnce({ ...baseOrder(), status: 'COMPLETED' })
    m.completeOrder.mockRejectedValueOnce(new ApiError('响应丢失', 503))
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text() === '完成订单')
    await btn!.trigger('click')
    await flushPromises()

    expect(m.completeOrder).toHaveBeenCalledTimes(1)
    expect(m.getOrder).toHaveBeenCalledTimes(2)
    const labels = w.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('完成订单')
  })

  it('503 后 GET 失败：锁定未知态，无旧 PENDING 按钮', async () => {
    setAuth('SHOP_OWNER')
    m.getOrder
      .mockResolvedValueOnce(baseOrder())
      .mockRejectedValueOnce(new ApiError('获取失败', 500))
    m.completeOrder.mockRejectedValueOnce(new ApiError('响应丢失', 503))
    const router = makeRouter()
    await router.push('/shop/orders/9007199254740997')
    await router.isReady()
    const w = track(
      mount(OrderDetailPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text() === '完成订单')
    await btn!.trigger('click')
    await flushPromises()

    expect(m.completeOrder).toHaveBeenCalledTimes(1)
    expect(m.getOrder).toHaveBeenCalledTimes(2)
    const labels = w.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('完成订单')
    expect(labels).not.toContain('取消订单')
  })

  it('scope 切换时丢弃迟到的旧 list 响应', async () => {
    setAuth('SHOP_OWNER', '20')
    let resolveOld: (v: unknown) => void = () => {}
    const oldPromise = new Promise((r) => {
      resolveOld = r
    })
    m.listOrders.mockReturnValueOnce(oldPromise)

    const router = makeRouter()
    await router.push('/shop/orders')
    await router.isReady()
    const w = track(
      mount(OrderListPage, { global: { plugins: [router], stubs: { ElButton } } }),
    )
    await flushPromises()

    m.listOrders.mockResolvedValueOnce(orderPage(baseOrder()))

    const auth = useAuthStore()
    auth.user = {
      id: '5',
      username: 'test',
      email: 't@example.com',
      role: 'SHOP_OWNER',
      merchant_id: '10',
      shop_id: '21',
    }
    await flushPromises()

    resolveOld(
      orderPage({
        ...baseOrder(),
        shop_id: '20',
      }),
    )
    await flushPromises()

    expect(m.listOrders).toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain('9007199254740997')
  })
})
