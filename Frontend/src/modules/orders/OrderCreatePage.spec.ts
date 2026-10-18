import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

const apiMocks = vi.hoisted(() => ({
  createOrder: vi.fn(),
}))

const catalogMocks = vi.hoisted(() => ({
  listSpus: vi.fn(),
  getSpu: vi.fn(),
}))

const inventoryMocks = vi.hoisted(() => ({
  getWarehouse: vi.fn(),
  listStocks: vi.fn(),
}))

vi.mock('@/modules/orders/api', () => ({
  createOrder: apiMocks.createOrder,
}))

vi.mock('@/modules/merchant/catalogApi', () => ({
  listSpus: catalogMocks.listSpus,
  getSpu: catalogMocks.getSpu,
}))

vi.mock('@/modules/merchant/inventoryApi', () => ({
  getWarehouse: inventoryMocks.getWarehouse,
  listStocks: inventoryMocks.listStocks,
}))

vi.mock('element-plus', async () => {
  const { h, defineComponent } = await import('vue')
  const helper = await import('@/modules/merchant/test-support/elementPlus')
  const ElCheckboxStub = defineComponent({
    name: 'ElCheckbox',
    props: {
      modelValue: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          type: 'checkbox',
          class: 'el-checkbox-stub',
          checked: Boolean(props.modelValue),
          disabled: Boolean(props.disabled),
          onChange: (event: Event) => {
            const target = event.target as HTMLInputElement | null
            emit('update:modelValue', Boolean(target?.checked))
          },
        })
    },
  })
  return {
    ...helper.elementPlusMock,
    ElCheckbox: ElCheckboxStub,
  }
})

import { useAuthStore } from '@/modules/auth/store'
import { ElButton as ElButtonStub } from '@/modules/merchant/test-support/elementPlus'
import { savePendingOrder } from '@/modules/orders/pendingOrder'
import OrderCreatePage from '@/modules/orders/OrderCreatePage.vue'

const createOrder = apiMocks.createOrder
const listSpus = catalogMocks.listSpus
const getSpu = catalogMocks.getSpu
const getWarehouse = inventoryMocks.getWarehouse
const listStocks = inventoryMocks.listStocks

const SKU_ID = '9007199254740997'
const SKU_ID_2 = '9007199254740999'
const SPU_ID = '11'
const MERCHANT_ID = '10'
const SHOP_ID = '20'
const USER_ID = '5'
const WAREHOUSE_ID = '40'

const UUID = '12345678-1234-4234-8234-123456789abc'

type Role = 'SHOP_OWNER' | 'SHOP_EMPLOYEE'

interface Scope {
  role: Role
  shopId: string
}

const OWNER: Scope = { role: 'SHOP_OWNER', shopId: SHOP_ID }
const EMPLOYEE: Scope = { role: 'SHOP_EMPLOYEE', shopId: SHOP_ID }

const spuRow = {
  id: SPU_ID,
  merchant_id: MERCHANT_ID,
  shop_id: SHOP_ID,
  name: '测试商品',
  status: 'ON_SALE',
  updated_at: '2024-05-01T10:00:00',
}

const skuA = {
  id: SKU_ID,
  spu_id: SPU_ID,
  sku_code: 'SKU-CODE-A',
  specification: '规格A',
  price: '9.95',
}

const skuB = {
  id: SKU_ID_2,
  spu_id: SPU_ID,
  sku_code: 'SKU-CODE-B',
  specification: '规格B',
  price: '10.00',
}

function makeDetail(skus: unknown[] = [skuA], status = 'ON_SALE') {
  return {
    ...spuRow,
    status,
    skus,
  }
}

function makeOrder(state: 'PENDING' | 'CANCELLED' = 'PENDING') {
  return {
    id: '777',
    merchant_id: MERCHANT_ID,
    shop_id: SHOP_ID,
    created_by_user_id: USER_ID,
    status: state,
    total_amount: '19.90',
    created_at: '2024-05-02T10:00:00',
    completed_at: null,
    cancelled_at: state === 'CANCELLED' ? '2024-05-02T11:00:00' : null,
    items: [
      {
        id: '1',
        sku_id: SKU_ID,
        quantity: '2',
        unit_price: '9.95',
        subtotal: '19.90',
        spu_name_snapshot: '测试商品',
        sku_code_snapshot: 'SKU-CODE-A',
        specification_snapshot: '规格A',
      },
    ],
  }
}

function pendingKey(scope: Scope) {
  return `seller.pending-order.v1:${USER_ID}:${MERCHANT_ID}:${scope.shopId}`
}

function buildRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/employee/orders/create', name: 'employee-create', component: OrderCreatePage },
      { path: '/shop/orders/create', name: 'shop-create', component: OrderCreatePage },
      { path: '/dashboard', name: 'dashboard', component: { template: '<div />' } },
      { path: '/employee/orders', name: 'employee-orders', component: { template: '<div />' } },
      { path: '/shop/orders', name: 'shop-orders', component: { template: '<div />' } },
      { path: '/employee/orders/:id', name: 'employee-order-detail', component: { template: '<div />' } },
      { path: '/shop/orders/:id', name: 'shop-order-detail', component: { template: '<div />' } },
    ],
  })
}

function seedAuth(scope: Scope) {
  const auth = useAuthStore()
  auth.$patch({
    token: 'test-token',
    identityState: 'ready',
    user: {
      id: USER_ID,
      username: 'test',
      email: 't@example.com',
      role: scope.role,
      merchant_id: MERCHANT_ID,
      shop_id: scope.shopId,
    },
  })
  return auth
}

interface MountOptions {
  path?: string
  scope?: Scope
}

async function mountPage(options: MountOptions = {}) {
  const scope = options.scope ?? OWNER
  const path = options.path ?? '/employee/orders/create'
  seedAuth(scope)
  const router = buildRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(OrderCreatePage, {
    global: {
      plugins: [router],
      stubs: {
        ElButton: ElButtonStub,
      },
    },
  })
  return { wrapper, router, scope }
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().trim() === text)
}

async function clickButton(wrapper: ReturnType<typeof mount>, text: string) {
  const button = findButtonByText(wrapper, text)
  expect(button, `button with text ${text}`).toBeTruthy()
  await button!.trigger('click')
  await flushPromises()
  return button!
}

function lastCreateArgs() {
  expect(createOrder).toHaveBeenCalled()
  return createOrder.mock.calls[createOrder.mock.calls.length - 1]
}

describe('OrderCreatePage', () => {
  let mounted: Array<ReturnType<typeof mount>> = []

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    sessionStorage.clear()
    setActivePinia(createPinia())
    mounted = []
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(
      UUID as `${string}-${string}-${string}-${string}-${string}`,
    )
    listSpus.mockResolvedValue({ items: [spuRow], total: 1 })
    getSpu.mockResolvedValue(makeDetail([skuA]))
    getWarehouse.mockResolvedValue({ id: WAREHOUSE_ID, merchant_id: MERCHANT_ID, name: '主仓' })
    listStocks.mockResolvedValue({ items: [], total: 0 })
  })

  afterEach(() => {
    for (const wrapper of mounted) {
      try {
        wrapper.unmount()
      } catch {
        // ignore unmount races
      }
    }
    mounted = []
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  async function mountTracked(options: MountOptions = {}) {
    const result = await mountPage(options)
    mounted.push(result.wrapper)
    return result
  }

  it('submits a single SKU with the exact quantity string and shows the successful order', async () => {
    createOrder.mockResolvedValue(makeOrder('PENDING'))
    listStocks.mockResolvedValue({
      items: [
        {
          warehouse_id: WAREHOUSE_ID,
          sku_id: SKU_ID,
          sku_code: 'SKU-CODE-A',
          spu_name: '测试商品',
          specification: '规格A',
          quantity: '42',
        },
      ],
      total: 1,
    })
    const { wrapper } = await mountTracked()
    await flushPromises()

    expect(listSpus).toHaveBeenCalled()
    expect(getSpu).not.toHaveBeenCalled()

    await clickButton(wrapper, '查看 SKU')
    expect(getSpu).toHaveBeenCalled()

    const checkbox = wrapper.find('input.el-checkbox-stub')
    expect(checkbox.exists()).toBe(true)
    await checkbox.setValue(true)
    await clickButton(wrapper, '加入购物清单')

    const qtyInput = wrapper.find('input.qty-input')
    expect(qtyInput.exists()).toBe(true)
    await qtyInput.setValue('3')
    expect((qtyInput.element as HTMLInputElement).value).toBe('3')

    await clickButton(wrapper, '保存并提交订单')

    const payload = JSON.stringify(lastCreateArgs())
    expect(payload).toContain(SKU_ID)
    expect(payload).toContain('"3"')

    expect(wrapper.text()).toContain('服务端确认的订单')
    expect(wrapper.text()).toContain('19.90')
    expect(wrapper.find('.workspace-success').text()).not.toBe('')
    expect(sessionStorage.getItem(pendingKey(OWNER))).toBeNull()
    expect(listStocks).toHaveBeenCalled()
  })

  it('submits multiple SKUs without altering the original arguments', async () => {
    getSpu.mockResolvedValue(makeDetail([skuA, skuB]))
    createOrder.mockResolvedValue(makeOrder('PENDING'))
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')

    const checkboxes = wrapper.findAll('input.el-checkbox-stub')
    expect(checkboxes.length).toBe(2)
    await checkboxes[0].setValue(true)
    await checkboxes[1].setValue(true)
    await clickButton(wrapper, '加入购物清单')

    const qtyInputs = wrapper.findAll('input.qty-input')
    expect(qtyInputs.length).toBe(2)
    await qtyInputs[0].setValue('2')
    await qtyInputs[1].setValue('7')

    await clickButton(wrapper, '保存并提交订单')

    const payload = JSON.stringify(lastCreateArgs())
    expect(payload).toContain(SKU_ID)
    expect(payload).toContain(SKU_ID_2)
    expect(payload).toContain('"2"')
    expect(payload).toContain('"7"')
    expect(wrapper.text()).toContain('服务端确认的订单')
  })

  it('keeps the pending key and items after a network failure, blocks new actions and never auto-resends', async () => {
    createOrder.mockRejectedValueOnce(new Error('network down'))
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')
    await wrapper.find('input.el-checkbox-stub').setValue(true)
    await clickButton(wrapper, '加入购物清单')
    await wrapper.find('input.qty-input').setValue('4')

    await clickButton(wrapper, '保存并提交订单')
    expect(createOrder).toHaveBeenCalledTimes(1)

    const stored = sessionStorage.getItem(pendingKey(OWNER))
    expect(stored).toBeTruthy()
    expect(stored).toContain(UUID)
    expect(stored).toContain(SKU_ID)
    expect(stored).toContain('"4"')

    const text = wrapper.text()
    expect(text).toContain(UUID)
    expect(text).toContain(SKU_ID)

    // 新建订单按钮仍渲染（供后续显式重试），但必须被禁用，不能再发起新请求。
    const submitButton = findButtonByText(wrapper, '保存并提交订单')
    expect(submitButton).toBeTruthy()
    expect(submitButton!.attributes('disabled')).toBeDefined()
    expect(findButtonByText(wrapper, '加入购物清单')).toBeUndefined()

    await flushPromises()
    await flushPromises()
    expect(createOrder).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem(pendingKey(OWNER))).toBe(stored)
  })

  it('retries explicitly with the same key and items and renders the CANCELLED replay correctly', async () => {
    createOrder.mockRejectedValueOnce(new Error('boom'))
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')
    await wrapper.find('input.el-checkbox-stub').setValue(true)
    await clickButton(wrapper, '加入购物清单')
    await wrapper.find('input.qty-input').setValue('5')
    await clickButton(wrapper, '保存并提交订单')

    const firstArgs = JSON.stringify(lastCreateArgs())
    const stored = JSON.parse(sessionStorage.getItem(pendingKey(OWNER)) ?? '{}') as {
      key?: string
      items?: unknown
    }
    expect(stored.key).toBe(UUID)

    createOrder.mockResolvedValue(makeOrder('CANCELLED'))

    await clickButton(wrapper, '按原请求重试')

    expect(createOrder).toHaveBeenCalledTimes(2)
    const secondArgs = JSON.stringify(lastCreateArgs())
    expect(secondArgs).toBe(firstArgs)
    expect(secondArgs).toContain(UUID)
    expect(secondArgs).toContain(SKU_ID)

    const text = wrapper.text()
    expect(text).toContain('服务端确认的订单')
    expect(text).toContain('取消')
    expect(text).not.toContain('存在未确认的订单请求')
    expect(sessionStorage.getItem(pendingKey(OWNER))).toBeNull()
  })

  it('surfaces a real 409 conflict while keeping the original record and shows no fake success', async () => {
    const conflict = Object.assign(new Error('请求冲突'), {
      status: 409,
      response: { status: 409, data: { message: '存在状态冲突' } },
    })
    createOrder.mockRejectedValueOnce(new Error('boom'))
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')
    await wrapper.find('input.el-checkbox-stub').setValue(true)
    await clickButton(wrapper, '加入购物清单')
    await wrapper.find('input.qty-input').setValue('6')
    await clickButton(wrapper, '保存并提交订单')

    createOrder.mockRejectedValueOnce(conflict)
    await clickButton(wrapper, '按原请求重试')

    const stored = sessionStorage.getItem(pendingKey(OWNER))
    expect(stored).toBeTruthy()
    expect(stored).toContain(UUID)

    const text = wrapper.text()
    expect(text).not.toContain('服务端确认的订单')
    expect(wrapper.find('.workspace-success').exists()).toBe(false)
    expect(text).toContain('存在未确认的订单请求')
  })

  it('does not call createOrder when sessionStorage.setItem throws', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')
    await wrapper.find('input.el-checkbox-stub').setValue(true)
    await clickButton(wrapper, '加入购物清单')
    await wrapper.find('input.qty-input').setValue('1')
    await clickButton(wrapper, '保存并提交订单')

    expect(createOrder).not.toHaveBeenCalled()
    expect(getWarehouse).not.toHaveBeenCalled()
    expect(listStocks).not.toHaveBeenCalled()
    expect(getSpu).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.workspace-success').exists()).toBe(false)

    setItemSpy.mockRestore()
  })

  it('does not allow adding or submitting SKUs of an off-sale product', async () => {
    getSpu.mockResolvedValue(makeDetail([skuA], 'OFF_SALE'))
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')

    const checkbox = wrapper.find('input.el-checkbox-stub')
    if (checkbox.exists()) {
      await checkbox.setValue(true)
    }
    const addButton = findButtonByText(wrapper, '加入购物清单')
    if (addButton && !addButton.attributes('disabled')) {
      await addButton.trigger('click')
      await flushPromises()
    }

    expect(wrapper.find('input.qty-input').exists()).toBe(false)
    const submitButton = findButtonByText(wrapper, '保存并提交订单')
    if (submitButton) {
      expect(submitButton.attributes('disabled')).toBeDefined()
    }
    expect(createOrder).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(pendingKey(OWNER))).toBeNull()
  })

  it('does not display foreign merchant products', async () => {
    listSpus.mockResolvedValue({
      items: [spuRow, { ...spuRow, id: '99', merchant_id: '11', name: '他人商品' }],
      total: 2,
    })
    const { wrapper } = await mountTracked()
    await flushPromises()

    // 生产页策略：列表中任一项不属于当前商家即整体隐藏并提示，绝不渲染任何商品。
    const text = wrapper.text()
    expect(text).toContain('服务端返回了不属于当前商家或非在售的商品，已全部隐藏。')
    expect(text).not.toContain('测试商品')
    expect(text).not.toContain('他人商品')
    expect(getSpu).not.toHaveBeenCalled()
  })

  it('only requests the SPUs listed by the backend and matches ids as strings', async () => {
    listSpus.mockResolvedValue({ items: [spuRow], total: 1 })
    const { wrapper } = await mountTracked()
    await flushPromises()

    await clickButton(wrapper, '查看 SKU')

    expect(getSpu).toHaveBeenCalledTimes(1)
    expect(getSpu).toHaveBeenCalledWith(SPU_ID)
    expect(JSON.stringify(getSpu.mock.calls[0])).toContain('"11"')
    expect(wrapper.findAll('input.el-checkbox-stub').length).toBe(1)
  })

  it('clears the draft and pending display when the scope changes but keeps the old scope storage', async () => {
    const oldKey = pendingKey(EMPLOYEE)
    const oldRecord = {
      key: UUID,
      created_at: '2024-05-02T10:00:00',
      items: [{ sku_id: SKU_ID, quantity: '9' }],
      merchant_id: MERCHANT_ID,
      shop_id: SHOP_ID,
      user_id: USER_ID,
    }
    savePendingOrder(oldRecord)
    const before = sessionStorage.getItem(oldKey)
    expect(before).toBeTruthy()

    const { wrapper, scope } = await mountTracked({ scope: EMPLOYEE })
    await flushPromises()

    expect(scope.role).toBe('SHOP_EMPLOYEE')
    expect(wrapper.text()).toContain('存在未确认的订单请求')
    expect(wrapper.text()).toContain(UUID)

    const auth = useAuthStore()
    auth.$patch({
      user: {
        id: USER_ID,
        username: 'test',
        email: 't@example.com',
        role: 'SHOP_EMPLOYEE',
        merchant_id: MERCHANT_ID,
        shop_id: '21',
      },
    })
    await flushPromises()
    await flushPromises()

    expect(sessionStorage.getItem(oldKey)).toBe(before)
    expect(wrapper.text()).not.toContain('存在未确认的订单请求')
    expect(wrapper.text()).not.toContain(UUID)
  })
})
