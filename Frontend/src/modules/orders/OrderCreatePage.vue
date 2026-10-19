<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElAlert, ElCheckbox, ElMessageBox } from 'element-plus'
import { createOrder } from '@/modules/orders/api'
import {
  MAX_DISTINCT_SKUS,
  PendingOrderStorageError,
  clearPendingOrder,
  isValidUuid,
  normalizeOrderItems,
  parseQuantity,
  readPendingOrder,
  savePendingOrder,
  updatePendingOrderState,
  type PendingOrder,
  type PendingOrderScope,
} from '@/modules/orders/pendingOrder'
import { getSpu, listSpus } from '@/modules/merchant/catalogApi'
import { getWarehouse, listStocks } from '@/modules/merchant/inventoryApi'
import { useIdentityScope } from '@/modules/workspace/useIdentityScope'
import type { Order, Page, Spu, SpuDetail, SpuStatus, Stock } from '@/shared/types/api'

/** 仅店主与员工可下单；复用现有身份范围协议。 */
const ALLOWED_ROLES = ['SHOP_OWNER', 'SHOP_EMPLOYEE'] as const
const PAGE_SIZE = 10
const ON_SALE: SpuStatus = 'ON_SALE'
const UUID_UNAVAILABLE = '当前浏览器环境无法生成安全幂等键（crypto.randomUUID 不可用），已取消本次下单请求。'

interface CartLine {
  skuId: string
  spuId: string
  skuCode: string
  specification: string
  price: string
  quantity: string
}

const spus = ref<Spu[]>([])
const listTotal = ref(0)
const listOffset = ref(0)
const listLoading = ref(false)
const listError = ref('')

const detail = ref<SpuDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

const selected = ref<Record<string, boolean>>({})
const cart = ref<CartLine[]>([])
const formError = ref('')

const pending = ref<PendingOrder | null>(null)
const pendingBroken = ref(false)
const pendingError = ref('')
const pendingLocked = ref(false)
const submitting = ref(false)

/** 清理确认框进行中的独立 busy，不复用提交态，也不跨 scope 生效。 */
const discardBusy = ref(false)

const createdOrder = ref<Order | null>(null)
const createError = ref('')
const createNotice = ref('')
const stockNotice = ref('')
const stockError = ref('')

let listSeq = 0
let detailSeq = 0
let createSeq = 0

const { auth, capture, current, explain } = useIdentityScope([...ALLOWED_ROLES], resetAll)

const scope = reactive({ userId: '', merchantId: '', shopId: '', role: '' })

const isShopOwner = computed(() => scope.role === 'SHOP_OWNER')
const isShopEmployee = computed(() => scope.role === 'SHOP_EMPLOYEE')

/** 真实路由前缀：店主 /shop，员工 /employee。 */
const listBasePath = computed(() => {
  if (isShopOwner.value) return '/shop/orders'
  if (isShopEmployee.value) return '/employee/orders'
  return ''
})

/** 返回链接只指向当前身份下的订单列表，不使用 /shops 或裸 /orders。 */
const homeLink = computed(() => {
  if (listBasePath.value === '') return null
  return { to: listBasePath.value, label: '返回订单列表' }
})

const createdOrderLink = computed(() => {
  const order = createdOrder.value
  if (order === null || listBasePath.value === '') return ''
  return `${listBasePath.value}/${encodeURIComponent(order.id)}`
})

const identityMissing = computed(() => scope.userId === '' || scope.merchantId === '' || scope.shopId === '')
const cartLocked = computed(() => pendingLocked.value || submitting.value)
const pageNo = computed(() => Math.floor(listOffset.value / PAGE_SIZE) + 1)
const detailSkus = computed(() => detail.value?.skus ?? [])
const detailMatchesShop = computed(
  () => detail.value !== null && scope.merchantId !== '' && detail.value.merchant_id === scope.merchantId,
)

const canCreate = computed(
  () => !cartLocked.value && !pendingBroken.value && !identityMissing.value && cart.value.length > 0,
)

const createdOutcomeNotice = computed(() => {
  const order = createdOrder.value
  if (order === null) return ''
  if (order.status === 'COMPLETED') return '服务端返回该订单已完成（可能为幂等重放），以服务端状态为准。'
  if (order.status === 'CANCELLED') return '服务端返回该订单已取消（可能为幂等重放），以服务端状态为准。'
  return '服务端已确认该请求对应的订单。'
})

const CORRUPTED_HINT =
  '待确认订单本地记录已丢失或损坏，无法判断此前请求的最终结果。请先人工核对历史订单，再按提示清理后重新下单。'

/** 幂等键只能来自 crypto.randomUUID；不可用则抛错阻止 POST，绝不随机回退。 */
function newUuid(): string {
  const cryptoApi = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  let value: unknown
  try {
    value = typeof cryptoApi?.randomUUID === 'function' ? cryptoApi.randomUUID() : undefined
  } catch {
    throw new Error(UUID_UNAVAILABLE)
  }
  if (typeof value !== 'string' || !isValidUuid(value)) throw new Error(UUID_UNAVAILABLE)
  return value
}

function currentScope(): PendingOrderScope {
  return { user_id: scope.userId, merchant_id: scope.merchantId, shop_id: scope.shopId }
}

function scopeReady(): boolean {
  return scope.userId !== '' && scope.merchantId !== '' && scope.shopId !== ''
}

function sameScope(a: PendingOrderScope, b: PendingOrderScope): boolean {
  return a.user_id === b.user_id && a.merchant_id === b.merchant_id && a.shop_id === b.shop_id
}

function recordMatches(record: PendingOrder, expected: PendingOrderScope): boolean {
  return (
    record.user_id === expected.user_id &&
    record.merchant_id === expected.merchant_id &&
    record.shop_id === expected.shop_id
  )
}

function clearCart(): void {
  cart.value = []
  selected.value = {}
}

/** scope 变化时关闭本页打开的确认框并清空商品/表单/结果；不删除任何存储记录。 */
function resetAll(): void {
  listSeq++
  detailSeq++
  createSeq++
  ElMessageBox.close()
  scope.userId = ''
  scope.merchantId = ''
  scope.shopId = ''
  scope.role = ''
  spus.value = []
  listTotal.value = 0
  listOffset.value = 0
  listLoading.value = false
  listError.value = ''
  detail.value = null
  detailLoading.value = false
  detailError.value = ''
  clearCart()
  formError.value = ''
  pending.value = null
  pendingBroken.value = false
  pendingError.value = ''
  pendingLocked.value = false
  submitting.value = false
  discardBusy.value = false
  createdOrder.value = null
  createError.value = ''
  createNotice.value = ''
  stockNotice.value = ''
  stockError.value = ''
}

function loadPending(): void {
  pending.value = null
  pendingBroken.value = false
  pendingError.value = ''
  pendingLocked.value = false
  if (!scopeReady()) return
  const ticket = capture()
  const expected = currentScope()
  let record: PendingOrder | null
  try {
    record = readPendingOrder(expected)
  } catch (cause) {
    if (!current(ticket)) return
    pendingBroken.value = true
    pendingError.value =
      cause instanceof PendingOrderStorageError
        ? `${cause.message} ${CORRUPTED_HINT}`
        : `${explain(cause)} ${CORRUPTED_HINT}`
    return
  }
  if (!current(ticket)) return
  if (record === null) return
  pending.value = record
  pendingLocked.value = true
  // 'submitting' 说明上次请求未回报结果，恢复为“未知”，绝不自动重发。
  if (record.state === 'submitting') {
    try {
      const restored = updatePendingOrderState(expected, 'unknown')
      if (current(ticket)) pending.value = restored
    } catch (cause) {
      if (!current(ticket)) return
      pendingError.value = `${explain(cause)} 已按“结果未知”处理，只能使用原请求重试。`
    }
  }
  syncCartFromPending(record)
}

/** 锁定态购物清单展示原记录明细，保证原 key + 原 items 重试。 */
function syncCartFromPending(record: PendingOrder): void {
  cart.value = record.items.map((item) => {
    const existing = cart.value.find((line) => line.skuId === item.sku_id)
    return {
      skuId: item.sku_id,
      spuId: existing?.spuId ?? '',
      skuCode: existing?.skuCode ?? '-',
      specification: existing?.specification ?? '（上次请求的明细）',
      price: existing?.price ?? '-',
      quantity: item.quantity,
    }
  })
  selected.value = {}
}

/** token / scopeEpoch / 身份任一变化都会 reset，并立即恢复当前合法 scope 后 loadPending。 */
watch(
  () => [
    auth.user?.id ?? '',
    auth.user?.merchant_id ?? '',
    auth.user?.shop_id ?? '',
    auth.user?.role ?? '',
    auth.token ?? '',
    auth.scopeEpoch ?? 0,
  ] as const,
  ([userId, merchantId, shopId, role]) => {
    const usable =
      userId !== '' &&
      merchantId !== null &&
      merchantId !== '' &&
      shopId !== null &&
      shopId !== '' &&
      (role === 'SHOP_OWNER' || role === 'SHOP_EMPLOYEE')
    if (!usable) {
      resetAll()
      return
    }
    const scopeChanged =
      scope.userId !== userId || scope.merchantId !== merchantId || scope.shopId !== shopId || scope.role !== role
    if (scopeChanged) resetAll()
    scope.userId = userId
    scope.merchantId = merchantId
    scope.shopId = shopId
    scope.role = role
    loadPending()
    void loadSpus(0)
  },
  { immediate: true, flush: 'sync' },
)

onBeforeUnmount(() => {
  ElMessageBox.close()
})

async function loadSpus(offset: number): Promise<void> {
  if (!scopeReady()) return
  const seq = ++listSeq
  const ticket = capture()
  const expected = currentScope()
  listLoading.value = true
  listError.value = ''
  try {
    const result: Page<Spu> = await listSpus(PAGE_SIZE, offset, { status: ON_SALE })
    if (seq !== listSeq || !current(ticket)) return
    const items = Array.isArray(result.items) ? result.items : []
    // 任一项不属于当前商家或不在售：整体隐藏，绝不渲染外来商品。
    const foreign = items.some((item) => item.merchant_id !== expected.merchant_id || item.status !== ON_SALE)
    if (foreign) {
      spus.value = []
      listTotal.value = 0
      listError.value = '服务端返回了不属于当前商家或非在售的商品，已全部隐藏。'
      return
    }
    spus.value = items
    listTotal.value = result.total
    listOffset.value = result.offset
  } catch (cause) {
    if (seq !== listSeq || !current(ticket)) return
    // 加载失败先清空旧商品，避免展示过期或跨 scope 数据。
    spus.value = []
    listTotal.value = 0
    listError.value = explain(cause)
  } finally {
    if (seq === listSeq && current(ticket)) listLoading.value = false
  }
}

function nextPage(): void {
  if (listOffset.value + PAGE_SIZE < listTotal.value) void loadSpus(listOffset.value + PAGE_SIZE)
}

function prevPage(): void {
  if (listOffset.value > 0) void loadSpus(Math.max(0, listOffset.value - PAGE_SIZE))
}

/** 详情必须再次校验商家归属与在售状态。 */
function detailIssue(detailValue: SpuDetail, expectedMerchantId: string): string | null {
  if (detailValue.merchant_id !== expectedMerchantId) return '该商品不属于当前商家，禁止下单。'
  if (detailValue.status !== ON_SALE) return '该商品当前不在售，禁止下单。'
  return null
}

async function openSpu(spu: Spu): Promise<void> {
  if (!scopeReady()) return
  const seq = ++detailSeq
  const ticket = capture()
  const expected = currentScope()
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  selected.value = {}
  try {
    const result = await getSpu(spu.id)
    if (seq !== detailSeq || !current(ticket)) return
    if (result.id !== spu.id) {
      detailError.value = '服务端返回的商品与所选商品不一致，已停止展示。'
      return
    }
    // 先校验归属/在售，通过后才赋值详情，绝不先展示外来详情。
    const issue = detailIssue(result, expected.merchant_id)
    if (issue !== null) {
      detailError.value = issue
      return
    }
    detail.value = result
    selected.value = {}
  } catch (cause) {
    if (seq !== detailSeq || !current(ticket)) return
    detail.value = null
    detailError.value = explain(cause)
  } finally {
    if (seq === detailSeq && current(ticket)) detailLoading.value = false
  }
}

function addSelectedSkus(): void {
  formError.value = ''
  if (cartLocked.value) {
    formError.value = '存在未确认的待重试订单，只能按原请求重试。'
    return
  }
  const currentDetail = detail.value
  if (currentDetail === null) {
    formError.value = '请先选择商品。'
    return
  }
  const issue = detailIssue(currentDetail, scope.merchantId)
  if (issue !== null) {
    formError.value = issue
    return
  }
  const chosen = currentDetail.skus.filter((sku) => selected.value[sku.id] === true)
  if (chosen.length === 0) {
    formError.value = '请至少勾选一个 SKU。'
    return
  }
  const next = cart.value.map((line) => ({ ...line }))
  for (const sku of chosen) {
    if (next.some((line) => line.skuId === sku.id)) continue
    next.push({
      skuId: sku.id,
      spuId: currentDetail.id,
      skuCode: sku.sku_code,
      specification: sku.specification,
      price: sku.price,
      quantity: '1',
    })
  }
  if (next.length > MAX_DISTINCT_SKUS) {
    formError.value = `一次最多提交 ${MAX_DISTINCT_SKUS} 个不同商品。`
    return
  }
  cart.value = next
  selected.value = {}
}

function removeLine(skuId: string): void {
  if (cartLocked.value) return
  cart.value = cart.value.filter((line) => line.skuId !== skuId)
  formError.value = ''
}

function normalizeQuantityForDisplay(line: CartLine): void {
  try {
    line.quantity = parseQuantity(line.quantity)
    formError.value = ''
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '数量必须是正整数。'
  }
}

/** 严格校验：1..100 个不同 SKU、正整数数量；仅发送 sku_id/quantity。 */
function validateCart(): { sku_id: string; quantity: string }[] {
  const lines = normalizeOrderItems(cart.value.map((line) => ({ sku_id: line.skuId, quantity: line.quantity })))
  cart.value = cart.value.map((line) => {
    const match = lines.find((item) => item.sku_id === line.skuId)
    return match === undefined ? line : { ...line, quantity: match.quantity }
  })
  return lines
}

async function createNewOrder(): Promise<void> {
  if (!canCreate.value || submitting.value) return
  if (pending.value !== null) {
    formError.value = '存在未确认的请求记录，无法新建订单。'
    return
  }
  formError.value = ''
  createError.value = ''
  createNotice.value = ''
  stockNotice.value = ''
  stockError.value = ''
  const ticket = capture()
  if (!current(ticket) || !scopeReady()) return
  const expected = currentScope()
  let key: string
  let items: { sku_id: string; quantity: string }[]
  try {
    items = validateCart()
    key = newUuid()
  } catch (cause) {
    formError.value = explain(cause)
    return
  }
  if (!current(ticket) || !sameScope(expected, currentScope())) return
  try {
    // 先保存准确记录（新 UUID + 原 items），保存失败绝不发送请求。
    const saved = savePendingOrder({
      key,
      items,
      user_id: expected.user_id,
      merchant_id: expected.merchant_id,
      shop_id: expected.shop_id,
    })
    if (!current(ticket)) return
    pending.value = saved
  } catch (cause) {
    if (!current(ticket)) return
    formError.value = `保存提交记录失败，已取消本次下单请求：${explain(cause)}`
    return
  }
  pendingLocked.value = true
  await sendRequest(pending.value as PendingOrder)
}

async function retryOriginal(): Promise<void> {
  const record = pending.value
  if (record === null || submitting.value || !scopeReady()) return
  if (!recordMatches(record, currentScope())) {
    pendingError.value = '待确认记录不属于当前身份范围，已拒绝重试。'
    return
  }
  await sendRequest(record)
}

/** 4xx 为服务端明确拒绝；网络错误与 5xx（及 409 冲突）才是结果未知。 */
function isExplicitRejection(status: number | undefined): boolean {
  return status !== undefined && status >= 400 && status < 500 && status !== 409
}

async function sendRequest(record: PendingOrder): Promise<void> {
  if (submitting.value) return
  const seq = ++createSeq
  const ticket = capture()
  if (!current(ticket)) return
  const expected = currentScope()
  if (!recordMatches(record, expected)) {
    pendingError.value = '待确认记录不属于当前身份范围，已拒绝重试。'
    return
  }
  submitting.value = true
  pendingLocked.value = true
  createError.value = ''
  createNotice.value = ''
  try {
    const marked = updatePendingOrderState(expected, 'submitting')
    if (current(ticket)) pending.value = marked
  } catch (cause) {
    if (!current(ticket)) return
    submitting.value = false
    pendingLocked.value = true
    createError.value = `本地状态标记失败，未发送下单请求：${explain(cause)} 原记录 ${record.key} 保留，请先核对后再处理。`
    return
  }
  if (!current(ticket) || seq !== createSeq) return
  // 请求体直接使用已保存记录：key 与 items 永不变换。
  let order: Order
  try {
    order = await createOrder(record.items, record.key)
  } catch (cause) {
    if (seq !== createSeq || !current(ticket)) return
    submitting.value = false
    const rawStatus = (cause as { status?: unknown } | null)?.status
    const status = typeof rawStatus === 'number' ? rawStatus : undefined
    if (isExplicitRejection(status)) {
      // 明确失败：显示真实业务错误，保留记录供人工核对。
      try {
        const restored = updatePendingOrderState(expected, 'unknown')
        if (current(ticket)) pending.value = restored
      } catch (markCause) {
        if (current(ticket)) pendingError.value = `本地状态写入失败：${explain(markCause)}`
      }
      pendingLocked.value = true
      createError.value =
        `下单被服务端明确拒绝（HTTP ${status}）：${explain(cause)} ` +
        `原记录（幂等键 ${record.key}）已保留供核对；本次拒绝不代表此前未知请求未成功，请先核对历史订单。`
      return
    }
    // 网络/5xx/409：结果未知，保留原 UUID/items，绝不自动重发。
    try {
      const restored = updatePendingOrderState(expected, 'unknown')
      if (current(ticket)) pending.value = restored
    } catch (markCause) {
      if (current(ticket)) pendingError.value = `结果未知，本地状态写入失败：${explain(markCause)}`
    }
    pendingLocked.value = true
    const prefix = status === undefined ? '请求未成功返回（网络异常）：' : `下单请求返回 HTTP ${status}：`
    createError.value =
      `${prefix}订单结果未知，已保留原幂等键与明细，禁止自动重发。` +
      `请先核对历史订单，再按原请求重试。服务端信息：${explain(cause)}`
    return
  }
  if (seq !== createSeq || !current(ticket)) return
  submitting.value = false
  await handleSuccess(ticket, record, order)
}

async function handleSuccess(ticket: ReturnType<typeof capture>, record: PendingOrder, order: Order): Promise<void> {
  if (!current(ticket)) return
  if (order.shop_id !== scope.shopId || order.merchant_id !== scope.merchantId) {
    pendingLocked.value = true
    createError.value = `服务端返回的订单不属于当前店铺，已停止展示；原请求 ${record.key} 保持锁定，请先人工核对历史订单。`
    return
  }
  createdOrder.value = order
  createNotice.value = '服务端已确认该请求对应的订单，金额与明细均来自服务端。'
  try {
    clearPendingOrder(currentScope())
    if (!current(ticket)) return
    pending.value = null
    pendingLocked.value = false
    clearCart()
    formError.value = ''
  } catch (cause) {
    // 仅本地清理失败：仍显示已确认成功并锁定旧请求，不误报业务失败。
    if (!current(ticket)) return
    pendingLocked.value = true
    createNotice.value = `服务端已确认该请求对应的订单（订单 ${order.id}）。本地待确认记录清理失败：${explain(cause)} 旧请求保持锁定，请勿重复下单。`
  }
  await loadStockHint(ticket, order)
}

/** 成功后查询真实库存；仓库与全部库存归属校验通过才展示，失败不改变成功结论。 */
async function loadStockHint(ticket: ReturnType<typeof capture>, order: Order): Promise<void> {
  stockNotice.value = ''
  stockError.value = ''
  const expected = currentScope()
  try {
    const warehouse = await getWarehouse()
    if (!current(ticket)) return
    if (warehouse === null || warehouse === undefined) {
      stockNotice.value = '当前店铺尚无可用仓库，无法查询库存（不影响订单确认结果）。'
      return
    }
    if (warehouse.merchant_id !== expected.merchant_id) {
      stockError.value = '服务端返回的仓库不属于当前商家，已停止展示库存信息（不影响订单确认结果）。'
      return
    }
    const stocks: { items: Stock[] } = await listStocks(20, 0)
    if (!current(ticket)) return
    const items = Array.isArray(stocks.items) ? stocks.items : []
    // 任何一条库存不属于该仓库：整体不展示，避免泄露外来信息。
    if (items.some((stock) => stock.warehouse_id !== warehouse.id)) {
      stockError.value = '服务端返回的库存不属于当前仓库，已停止展示库存信息（不影响订单确认结果）。'
      return
    }
    const quantities = new Map<string, string[]>()
    for (const stock of items) {
      const list = quantities.get(stock.sku_id) ?? []
      list.push(stock.quantity)
      quantities.set(stock.sku_id, list)
    }
    const parts = order.items.map((line) => {
      const found = quantities.get(line.sku_id)
      if (found === undefined || found.length === 0) return `${line.sku_code_snapshot}：库存未返回`
      return `${line.sku_code_snapshot}：服务端库存 ${found.join('/')}`
    })
    stockNotice.value = `${parts.join('；')}（库存为服务端原值，仅供参考，下单结果不依赖本页计算）`
  } catch (cause) {
    if (!current(ticket)) return
    stockError.value = `订单已确认成功，但库存查询失败：${explain(cause)} 成功结论不变。`
  }
}

/**
 * 危险跨 scope 清理防护：开始即 capture + 当前 scope/旧记录 key 快照，
 * 确认后仅当 ticket 仍有效、scope 未变且原 key 一致，才清理原 scope。
 */
async function discardPending(): Promise<void> {
  if (discardBusy.value || submitting.value || pending.value === null) return
  const ticket = capture()
  const originalScope = currentScope()
  const originalKey = pending.value.key
  discardBusy.value = true
  try {
    await ElMessageBox.confirm(
      '清理后无法再用原幂等键重试。请先核对历史订单，确认该请求未产生订单。',
      '确认放弃未确认的请求',
      { confirmButtonText: '我已人工核对订单，放弃该请求', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  } finally {
    if (current(ticket)) discardBusy.value = false
  }
  if (!current(ticket) || !sameScope(originalScope, currentScope())) return
  let latestKey: string | null
  try {
    latestKey = readPendingOrder(originalScope)?.key ?? null
  } catch {
    latestKey = null
  }
  if (latestKey !== originalKey) {
    if (current(ticket)) pendingError.value = '待确认记录在确认期间已变化，已取消本次清理。'
    return
  }
  discardBusy.value = true
  try {
    clearPendingOrder(originalScope)
    if (!current(ticket) || !sameScope(originalScope, currentScope())) return
    pending.value = null
    pendingLocked.value = false
    pendingError.value = ''
    createError.value = ''
    createNotice.value = '已按人工核对结果放弃该请求的本地记录；若此前请求实际成功，以服务端订单为准。'
    clearCart()
  } catch (cause) {
    if (!current(ticket)) return
    pendingError.value = `清理待确认记录失败：${explain(cause)}`
  } finally {
    // 只清理同一 scope 的 busy，绝不清新 scope 的状态。
    if (current(ticket)) discardBusy.value = false
  }
}

async function discardBroken(): Promise<void> {
  if (discardBusy.value || !pendingBroken.value || submitting.value) return
  const ticket = capture()
  const originalScope = currentScope()
  discardBusy.value = true
  try {
    await ElMessageBox.confirm(
      '该记录无法解析，无法自动判断此前请求是否成功。请先人工核对历史订单，再清理本地记录。',
      '确认清理损坏的待确认记录',
      { confirmButtonText: '我已人工核对订单，放弃该请求', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  } finally {
    if (current(ticket)) discardBusy.value = false
  }
  if (!current(ticket) || !sameScope(originalScope, currentScope())) return
  discardBusy.value = true
  let stillBroken = false
  try {
    readPendingOrder(originalScope)
  } catch {
    stillBroken = true
  }
  if (!stillBroken) {
    if (current(ticket)) pendingError.value = '损坏记录在确认期间已变化，已取消本次清理。'
    if (current(ticket)) discardBusy.value = false
    return
  }
  try {
    clearPendingOrder(originalScope)
    if (!current(ticket) || !sameScope(originalScope, currentScope())) return
    pendingBroken.value = false
    pendingError.value = ''
    createNotice.value = '已清理损坏的本地记录，请先人工核对历史订单后再下单。'
  } catch (cause) {
    if (!current(ticket)) return
    pendingError.value = `清理待确认记录失败：${explain(cause)}`
  } finally {
    if (current(ticket)) discardBusy.value = false
  }
}

function formatOrderStatus(status: Order['status']): string {
  if (status === 'COMPLETED') return '已完成'
  if (status === 'CANCELLED') return '已取消'
  return '待处理'
}

function pendingStateText(state: PendingOrder['state'] | undefined): string {
  return state === 'submitting' ? '提交中（恢复为结果未知）' : '结果未知/待核对'
}
</script>

<template>
  <div class="merchant-page">
    <div class="page-head">
      <div>
        <h1>新建订单</h1>
        <p class="hint">只发送商品与数量；商家、店铺、下单人与价格均由服务端决定，本页不计算金额。</p>
      </div>
      <router-link v-if="homeLink" :to="homeLink.to">
        <el-button>{{ homeLink.label }}</el-button>
      </router-link>
    </div>

    <el-alert
      class="notice"
      type="warning"
      :closable="false"
      show-icon
      title="记录丢失无法自动证明请求失败：如曾提交下单，请先核对历史订单，再决定是否重新下单。"
    />

    <div v-if="identityMissing" class="merchant-card workspace-error">
      当前账号缺少可用的商家或店铺身份（需店主或员工身份），无法下单。
    </div>

    <template v-else>
      <div class="merchant-card">
        <h2>当前身份</h2>
        <dl class="meta">
          <dt>用户编号</dt><dd>{{ scope.userId }}</dd>
          <dt>商家编号</dt><dd>{{ scope.merchantId }}</dd>
          <dt>店铺编号</dt><dd>{{ scope.shopId }}</dd>
          <dt>身份</dt><dd>{{ isShopOwner ? '店主' : '员工' }}</dd>
        </dl>
      </div>

      <div v-if="pendingBroken" class="merchant-card workspace-error">
        <h2>待确认订单记录不可用</h2>
        <p>{{ pendingError }}</p>
        <p>为避免重复下单，已阻止新建订单。请先人工核对历史订单，再清理该记录。</p>
        <el-button type="danger" :disabled="submitting" @click="discardBroken">我已人工核对，清理损坏记录</el-button>
      </div>

      <div v-else-if="pending" class="merchant-card pending-card">
        <h2>存在未确认的订单请求</h2>
        <p class="workspace-error">
          状态：{{ pendingStateText(pending.state) }}。该请求可能已成功，禁止自动重发或更换幂等键；请先核对历史订单，再按原请求重试。
        </p>
        <p v-if="pendingError" class="workspace-error">{{ pendingError }}</p>
        <dl class="meta">
          <dt>幂等键</dt><dd class="mono">{{ pending.key }}</dd>
          <dt>创建时间</dt><dd>{{ pending.created_at }}</dd>
        </dl>
        <div class="table-wrap">
          <table class="merchant-table">
            <thead><tr><th>SKU 编号</th><th>数量</th></tr></thead>
            <tbody>
              <tr v-for="item in pending.items" :key="item.sku_id">
                <td class="mono">{{ item.sku_id }}</td>
                <td>{{ item.quantity }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="actions">
          <el-button type="primary" :loading="submitting" :disabled="submitting" @click="retryOriginal">
            按原请求重试
          </el-button>
          <el-button type="danger" :disabled="submitting" @click="discardPending">
            我已人工核对订单，放弃该请求
          </el-button>
        </div>
      </div>

      <div v-else class="merchant-card">
        <h2>选择在售商品</h2>
        <div class="actions">
          <el-button :disabled="listLoading || listOffset === 0" @click="prevPage">上一页</el-button>
          <span>第 {{ pageNo }} 页 / 共 {{ listTotal }} 条（每页 {{ PAGE_SIZE }}）</span>
          <el-button :disabled="listLoading || listOffset + PAGE_SIZE >= listTotal" @click="nextPage">下一页</el-button>
        </div>
        <p v-if="listError" class="workspace-error">{{ listError }}</p>
        <div class="table-wrap">
          <table class="merchant-table">
            <thead><tr><th>商品</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="spu in spus" :key="spu.id">
                <td>{{ spu.name }}</td>
                <td>{{ spu.status }}</td>
                <td>{{ spu.updated_at }}</td>
                <td>
                  <el-button size="small" :disabled="detailLoading" @click="openSpu(spu)">查看 SKU</el-button>
                </td>
              </tr>
              <tr v-if="spus.length === 0"><td colspan="4">暂无可选在售商品。</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="!pendingBroken && !pending && detail" class="merchant-card">
        <h2>商品 SKU：{{ detail.name }}</h2>
        <p v-if="detailError" class="workspace-error">{{ detailError }}</p>
        <div class="table-wrap">
          <table class="merchant-table">
            <thead><tr><th>选择</th><th>SKU 编号</th><th>编码</th><th>规格</th><th>单价</th></tr></thead>
            <tbody>
              <tr v-for="sku in detailSkus" :key="sku.id">
                <td><el-checkbox v-model="selected[sku.id]" :disabled="cartLocked" :aria-label="`选择 SKU ${sku.sku_code}`" /></td>
                <td class="mono">{{ sku.id }}</td>
                <td>{{ sku.sku_code }}</td>
                <td>{{ sku.specification }}</td>
                <td>{{ sku.price }}</td>
              </tr>
              <tr v-if="detailSkus.length === 0"><td colspan="5">该商品暂无 SKU。</td></tr>
            </tbody>
          </table>
        </div>
        <div class="actions">
          <el-button type="primary" :disabled="cartLocked || !detailMatchesShop || detailError !== ''" @click="addSelectedSkus">
            加入购物清单
          </el-button>
        </div>
      </div>

      <div v-if="!pendingBroken" class="merchant-card">
        <h2>购物清单</h2>
        <p v-if="pendingLocked" class="workspace-error">
          存在未确认的请求记录，购物清单已锁定，只能按原请求重试或人工核对后放弃。
        </p>
        <p v-if="formError" class="workspace-error">{{ formError }}</p>
        <div class="table-wrap">
          <table class="merchant-table">
            <thead>
              <tr><th>SKU 编号</th><th>编码</th><th>规格</th><th>单价</th><th>数量（正整数 1..9223372036854775807）</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="line in cart" :key="line.skuId">
                <td class="mono">{{ line.skuId }}</td>
                <td>{{ line.skuCode }}</td>
                <td>{{ line.specification }}</td>
                <td>{{ line.price }}</td>
                <td>
                  <input
                    v-model="line.quantity"
                    :aria-label="`SKU ${line.skuCode} 的数量`"
                    class="qty-input"
                    type="text"
                    inputmode="numeric"
                    :disabled="cartLocked"
                    @blur="normalizeQuantityForDisplay(line)"
                  />
                </td>
                <td><el-button size="small" :disabled="cartLocked" @click="removeLine(line.skuId)">移除</el-button></td>
              </tr>
              <tr v-if="cart.length === 0"><td colspan="6">购物清单为空。</td></tr>
            </tbody>
          </table>
        </div>
        <p class="hint">
          共 {{ cart.length }} 个不同 SKU（上限 {{ MAX_DISTINCT_SKUS }}）；订单金额以服务端返回为准。
        </p>
        <div class="actions">
          <el-button type="primary" :loading="submitting" :disabled="!canCreate || submitting" @click="createNewOrder">
            保存并提交订单
          </el-button>
        </div>
      </div>
    </template>

    <div v-if="createNotice" class="merchant-card workspace-success">{{ createNotice }}</div>
    <div v-if="createError" class="merchant-card workspace-error">{{ createError }}</div>

    <div v-if="createdOrder" class="merchant-card">
      <h2>服务端确认的订单</h2>
      <dl class="meta">
        <dt>订单编号</dt><dd class="mono">{{ createdOrder.id }}</dd>
        <dt>商家编号</dt><dd>{{ createdOrder.merchant_id }}</dd>
        <dt>店铺编号</dt><dd>{{ createdOrder.shop_id }}</dd>
        <dt>订单状态</dt><dd>{{ formatOrderStatus(createdOrder.status) }}</dd>
        <dt>订单金额</dt><dd>{{ createdOrder.total_amount }}</dd>
        <dt>下单人</dt><dd>{{ createdOrder.created_by_user_id }}</dd>
        <dt>创建时间</dt><dd>{{ createdOrder.created_at }}</dd>
      </dl>
      <p class="hint">{{ createdOutcomeNotice }}</p>
      <div class="table-wrap">
        <table class="merchant-table">
          <thead><tr><th>商品</th><th>规格</th><th>数量</th><th>单价</th><th>小计</th></tr></thead>
          <tbody>
            <tr v-for="item in createdOrder.items" :key="item.id">
              <td>{{ item.spu_name_snapshot }}（{{ item.sku_code_snapshot }}）</td>
              <td>{{ item.specification_snapshot }}</td>
              <td>{{ item.quantity }}</td>
              <td>{{ item.unit_price }}</td>
              <td>{{ item.subtotal }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="stockNotice" class="hint">{{ stockNotice }}</p>
      <p v-if="stockError" class="workspace-error">{{ stockError }}</p>
      <div class="actions">
        <router-link :to="createdOrderLink">
          <el-button type="primary">查看真实订单</el-button>
        </router-link>
        <el-button :disabled="submitting" @click="loadPending">刷新待确认状态</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.page-head h1 { margin: 0 0 4px; }
.hint { color: #606266; font-size: 13px; margin: 4px 0 8px; }
.notice { margin-bottom: 12px; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
.meta { display: grid; grid-template-columns: 120px 1fr; gap: 4px 12px; margin: 8px 0; }
.meta dt { color: #909399; }
.meta dd { margin: 0; word-break: break-all; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.table-wrap { width: 100%; overflow-x: auto; }
.qty-input { width: 12rem; max-width: 100%; padding: 4px 8px; border: 1px solid #dcdfe6; border-radius: 4px; box-sizing: border-box; }
.pending-card { border-left: 4px solid #e6a23c; }
@media (max-width: 600px) {
  .meta { grid-template-columns: 1fr; }
  .qty-input { width: 100%; }
}
</style>
