<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { getOrder } from '@/modules/merchant/api'
import { getWarehouse, listStocks } from '@/modules/merchant/inventoryApi'
import { cancelOrder, completeOrder } from '@/modules/orders/api'
import { useIdentityScope } from '@/modules/workspace/useIdentityScope'
import { ApiError } from '@/shared/http/errors'
import { formatDecimal, formatNaiveDateTime } from '@/shared/http/precision'
import type { IdentityRole, Order, Stock, Warehouse } from '@/shared/types/api'

const ALLOWED: IdentityRole[] = ['SHOP_OWNER', 'SHOP_EMPLOYEE']
const STATUS_LABELS: Record<string, string> = { PENDING: '待完成', COMPLETED: '已完成', CANCELLED: '已取消' }
const UNKNOWN_MESSAGE = '提交结果未知：网络中断或服务端暂时不可用，系统没有自动重发，避免重复完成或取消。请先查看服务器上的真实订单状态，再决定是否操作。'

const route = useRoute()
const order = ref<Order | null>(null)
const loading = ref(false)
const error = ref('')
const notFound = ref(false)
const busy = ref(false)
const notice = ref('')
const noticeKind = ref<'success' | 'info'>('success')
const unknown = ref('')
const stockLoading = ref(false)
const stockError = ref('')
const warehouse = ref<Warehouse | null>(null)
const stocks = ref<Stock[]>([])
let loadSequence = 0
let confirmTicket = 0

const scope = useIdentityScope(ALLOWED, () => {
  loadSequence++
  confirmTicket++
  order.value = null
  loading.value = false
  error.value = ''
  notFound.value = false
  busy.value = false
  notice.value = ''
  unknown.value = ''
  stockLoading.value = false
  stockError.value = ''
  warehouse.value = null
  stocks.value = []
  ElMessageBox.close()
})

const role = computed(() => scope.auth.user?.role ?? null)
const permitted = computed(() => role.value !== null && ALLOWED.includes(role.value))
const listPrefix = computed(() => (role.value === 'SHOP_EMPLOYEE' ? '/employee' : role.value === 'SHOP_OWNER' ? '/shop' : null))
const orderId = computed(() => {
  const value = route.params.id
  return typeof value === 'string' && value.length > 0 ? value : ''
})
const isPending = computed(() => order.value?.status === 'PENDING')
const canAct = computed(() => permitted.value && isPending.value && !busy.value && !loading.value && unknown.value === '')

/** Staff may view every order of their own shop; only merchant/shop must match the live identity. */
function belongsToScope(value: Order): boolean {
  const user = scope.auth.user
  if (!user) return false
  if (value.merchant_id !== user.merchant_id) return false
  return value.shop_id === user.shop_id
}

function optionalDate(value: string | null): string {
  return value === null || value === '' ? '—' : formatNaiveDateTime(value)
}

/** Every returned row must belong to the live merchant warehouse; otherwise nothing is shown. */
function stocksInWarehouse(): Stock[] {
  const found = warehouse.value
  const user = scope.auth.user
  if (!found || !user || found.merchant_id !== user.merchant_id) return []
  if (!stocks.value.every((stock) => stock.warehouse_id === found.id)) return []
  return stocks.value
}

function showNotice(message: string, kind: 'success' | 'info'): void {
  noticeKind.value = kind
  notice.value = message
}

function showIdentityFailure(): void {
  notFound.value = true
  error.value = '订单不存在，或不属于当前商家与店铺范围，已隐藏该订单。'
}

async function load() {
  if (!permitted.value) return
  const id = orderId.value
  const captured = scope.capture()
  const request = ++loadSequence
  order.value = null
  loading.value = false
  error.value = ''
  notFound.value = false
  notice.value = ''
  unknown.value = ''
  warehouse.value = null
  stocks.value = []
  stockLoading.value = false
  stockError.value = ''
  if (id === '') {
    error.value = '订单编号缺失，无法读取订单详情。'
    return
  }
  loading.value = true
  try {
    const value = await getOrder(id)
    if (!scope.current(captured) || request !== loadSequence) return
    if (!belongsToScope(value)) {
      order.value = null
      showIdentityFailure()
      return
    }
    order.value = value
  } catch (cause) {
    if (!scope.current(captured) || request !== loadSequence) return
    if (cause instanceof ApiError && cause.status === 404) {
      notFound.value = true
      error.value = '订单不存在，或当前账号无权查看此订单。'
    } else {
      error.value = scope.explain(cause)
    }
  } finally {
    if (scope.current(captured) && request === loadSequence) loading.value = false
  }
}

/** Re-read the real order (then stock); true only when the order itself was fetched successfully. */
async function reload(showStock: boolean, successMessage?: string): Promise<boolean> {
  if (!permitted.value) return false
  const id = orderId.value
  const captured = scope.capture()
  const request = ++loadSequence
  loading.value = true
  order.value = null
  error.value = ''
  notFound.value = false
  stockLoading.value = false
  stockError.value = ''
  warehouse.value = null
  stocks.value = []
  if (successMessage) showNotice(successMessage, 'success')
  if (id === '') {
    unknown.value = ''
    error.value = '订单编号缺失，无法读取订单详情。'
    loading.value = false
    return false
  }
  try {
    const value = await getOrder(id)
    if (!scope.current(captured) || request !== loadSequence) return false
    unknown.value = ''
    if (!belongsToScope(value)) {
      order.value = null
      showIdentityFailure()
      return false
    }
    order.value = value
    return true
  } catch (cause) {
    if (!scope.current(captured) || request !== loadSequence) return false
    unknown.value = ''
    if (cause instanceof ApiError && cause.status === 404) {
      notFound.value = true
      error.value = '订单不存在，或当前账号无权查看此订单。'
      return false
    }
    const message = scope.explain(cause)
    if (successMessage) {
      showNotice(`${successMessage} 重新查询订单状态失败，请勿重复提交。`, 'success')
      unknown.value = `订单写入已成功，但重新读取真实状态失败：${message} 请以服务器记录为准，不要重复提交。`
    } else {
      unknown.value = `重新读取订单状态失败：${message} 请先确认服务器上的真实状态，不要重复提交。`
    }
    error.value = message
    return false
  } finally {
    if (scope.current(captured) && request === loadSequence) {
      loading.value = false
      if (showStock && order.value) {
        const stockRequest = ++loadSequence
        void readStock(stockRequest, captured)
      }
    }
  }
}

async function readStock(request: number, captured: ReturnType<typeof scope.capture>) {
  if (!scope.current(captured) || request !== loadSequence) return
  stockLoading.value = true
  stockError.value = ''
  warehouse.value = null
  stocks.value = []
  try {
    const found = await getWarehouse()
    if (!scope.current(captured) || request !== loadSequence) return
    const user = scope.auth.user
    if (!user || found.merchant_id !== user.merchant_id) {
      warehouse.value = null
      stocks.value = []
      stockError.value = '服务器返回的仓库不属于当前商家，已隐藏库存信息。'
      return
    }
    const result = await listStocks(20, 0)
    if (!scope.current(captured) || request !== loadSequence) return
    if (!result.items.every((stock) => stock.warehouse_id === found.id)) {
      warehouse.value = null
      stocks.value = []
      stockError.value = '服务器返回的库存不属于当前归属仓库，已隐藏全部库存信息。'
      return
    }
    warehouse.value = found
    stocks.value = result.items
  } catch (cause) {
    if (!scope.current(captured) || request !== loadSequence) return
    stockError.value = scope.explain(cause)
  } finally {
    if (scope.current(captured) && request === loadSequence) stockLoading.value = false
  }
}

/** True when the write outcome is unknown: no response at all, or a 5xx server failure. */
function isUnknownOutcome(cause: unknown): boolean {
  if (!(cause instanceof ApiError)) return true
  if (cause.status === undefined) return true
  return cause.status >= 500
}

async function submit(action: 'complete' | 'cancel') {
  if (!canAct.value) return
  const current = order.value
  if (!current || current.status !== 'PENDING') return
  const label = action === 'complete' ? '完成' : '取消'
  const id = current.id
  const captured = scope.capture()
  const request = ++loadSequence
  // The whole confirm + request window stays busy so a second dialog cannot open.
  busy.value = true
  notice.value = ''
  unknown.value = ''
  error.value = ''
  const ticket = ++confirmTicket
  try {
    await ElMessageBox.confirm(
      `确认${label}订单 ${id} 吗？该操作会按服务器当前状态落库，请勿重复提交。`,
      `${label}订单`,
      { type: 'warning', confirmButtonText: `确认${label}`, cancelButtonText: '再想想' },
    )
  } catch {
    if (ticket === confirmTicket) confirmTicket++
    if (scope.current(captured) && request === loadSequence) busy.value = false
    return
  }
  if (!scope.current(captured) || request !== loadSequence || confirmTicket !== ticket) return
  confirmTicket++
  const successMessage = `订单已${label}。`
  try {
    // Complete/cancel are not idempotency-keyed: they are sent exactly once per confirmation.
    if (action === 'complete') await completeOrder(id)
    else await cancelOrder(id)
    if (!scope.current(captured) || request !== loadSequence) return
    busy.value = false
    showNotice(successMessage, 'success')
    const refreshed = await reload(true, successMessage)
    if (!scope.current(captured) || !permitted.value) return
    if (!refreshed && unknown.value === '' && notice.value === successMessage) {
      unknown.value = `${successMessage} 但重新读取订单状态未成功，请勿重复提交。`
    }
  } catch (cause) {
    if (!scope.current(captured) || request !== loadSequence) return
    busy.value = false
    if (cause instanceof ApiError && cause.status === 409) {
      const conflict = scope.explain(cause)
      showNotice(`订单状态已变化（状态冲突）：${conflict}`, 'info')
      unknown.value = ''
      const refreshed = await reload(true)
      if (!scope.current(captured) || !permitted.value) return
      if (refreshed) error.value = ''
      else error.value = `${conflict} 已尝试重新查询最新状态但未成功，请勿重复提交。`
      return
    }
    if (isUnknownOutcome(cause)) {
      // Never resend the POST; only a GET may learn the real state.
      const refreshed = await reload(true)
      if (!scope.current(captured) || !permitted.value) return
      if (refreshed) {
        showNotice('提交结果未确认，已重新查询服务器上的真实订单状态，请按当前状态操作，不要重复提交。', 'info')
        error.value = ''
        unknown.value = ''
      } else {
        showNotice('提交结果可能未成功，且重新查询失败；请勿重复提交。', 'info')
        unknown.value = UNKNOWN_MESSAGE
      }
      return
    }
    error.value = scope.explain(cause)
  }
}

watch(() => route.params.id, () => {
  loadSequence++
  confirmTicket++
  busy.value = false
  notice.value = ''
  unknown.value = ''
  void load()
})
watch(scope.revision, () => {
  if (!permitted.value) return
  if (scope.auth.user && orderId.value !== '') void load()
}, { flush: 'post' })
onScopeDispose(() => {
  loadSequence++
  confirmTicket++
  ElMessageBox.close()
})
onMounted(() => { if (permitted.value && orderId.value !== '') void load() })
</script>
<template>
  <div class="merchant-page">
    <div class="welcome-row">
      <div>
        <div class="page-eyebrow">ORDER SNAPSHOT</div>
        <h1>订单详情</h1>
        <p>订单编号：{{ orderId || '（缺失）' }}；金额、单价与小计均按下单时的服务端快照原文展示，不使用当前商品价格。</p>
      </div>
      <div>
        <RouterLink v-if="listPrefix" :to="`${listPrefix}/orders`"><el-button>返回订单列表</el-button></RouterLink>
        <el-button :disabled="!permitted || loading || busy" @click="load()">刷新</el-button>
      </div>
    </div>

    <p v-if="!permitted" class="workspace-error" role="alert">当前身份无权查看订单详情，请确认身份后刷新。</p>

    <template v-else>
      <p v-if="error" class="workspace-error" role="alert">
        {{ error }}
        <el-button v-if="!notFound && orderId" link :disabled="loading" @click="reload(true)">重新查询真实状态</el-button>
      </p>
      <p v-if="unknown" class="workspace-error" role="alert">
        {{ unknown }}
        <el-button link :disabled="loading || busy" @click="reload(true)">重新查询真实状态</el-button>
      </p>
      <p v-if="notice" class="workspace-warning" role="status">{{ notice }}</p>

      <section class="merchant-card">
        <h2>订单信息</h2>
        <p v-if="loading" class="list-state">正在读取订单详情…</p>
        <p v-else-if="!order" class="list-state">{{ notFound ? '该订单不可见。' : '暂无订单信息。' }}</p>
        <template v-else>
          <div class="order-facts">
            <div><small>订单状态</small><strong>{{ STATUS_LABELS[order.status] ?? order.status }}</strong></div>
            <div><small>订单总额（快照）</small><strong>¥{{ formatDecimal(order.total_amount) }}</strong></div>
            <div><small>商家编号</small><strong>{{ order.merchant_id }}</strong></div>
            <div><small>店铺编号</small><strong>{{ order.shop_id }}</strong></div>
            <div><small>创建人编号</small><strong>{{ order.created_by_user_id }}</strong></div>
            <div><small>创建时间</small><strong>{{ formatNaiveDateTime(order.created_at) }}</strong></div>
            <div><small>完成时间</small><strong>{{ optionalDate(order.completed_at) }}</strong></div>
            <div><small>取消时间</small><strong>{{ optionalDate(order.cancelled_at) }}</strong></div>
          </div>
          <div v-if="isPending" class="workspace-tabs">
            <el-button type="primary" :disabled="!canAct" :loading="busy" @click="submit('complete')">完成订单</el-button>
            <el-button :disabled="!canAct" :loading="busy" @click="submit('cancel')">取消订单</el-button>
            <span v-if="!isPending" class="list-state">仅“待完成”订单可以完成或取消。</span>
          </div>
        </template>
      </section>

      <section v-if="order" class="merchant-card">
        <h2>订单明细（历史快照）</h2>
        <div v-if="!order.items.length" class="list-state">该订单没有明细。</div>
        <div v-else class="table-wrap">
          <table class="merchant-table">
            <thead><tr><th>商品名称快照</th><th>SKU 编码快照</th><th>规格快照</th><th>数量</th><th>单价快照</th><th>小计快照</th></tr></thead>
            <tbody>
              <tr v-for="item in order.items" :key="item.id">
                <td>{{ item.spu_name_snapshot }}</td>
                <td>{{ item.sku_code_snapshot }}</td>
                <td>{{ item.specification_snapshot }}</td>
                <td>{{ item.quantity }}</td>
                <td>¥{{ formatDecimal(item.unit_price) }}</td>
                <td>¥{{ formatDecimal(item.subtotal) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="order" class="merchant-card">
        <h2>仓库库存</h2>
        <p>库存数据来自服务器返回的当前归属仓库，页面不自行推算或扣减。</p>
        <p v-if="stockLoading" class="list-state">正在读取库存…</p>
        <p v-else-if="stockError" class="workspace-error" role="alert">
          {{ stockError }}
          <el-button link :disabled="loading" @click="reload(true)">重试</el-button>
        </p>
        <template v-else>
          <p v-if="!warehouse" class="list-state">尚未读取库存；完成或取消后将查询真实库存。</p>
          <template v-else>
            <p class="list-state">仓库：{{ warehouse.name }}（{{ warehouse.id }}）</p>
            <div v-if="!stocksInWarehouse().length" class="list-state">该仓库暂无可展示的库存记录。</div>
            <div v-else class="table-wrap">
              <table class="merchant-table">
                <thead><tr><th>SKU 编码</th><th>商品名称</th><th>规格</th><th>库存数量</th></tr></thead>
                <tbody>
                  <tr v-for="stock in stocksInWarehouse()" :key="`${stock.warehouse_id}-${stock.sku_id}`">
                    <td>{{ stock.sku_code }}</td>
                    <td>{{ stock.spu_name }}</td>
                    <td>{{ stock.specification }}</td>
                    <td>{{ stock.quantity }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </template>
      </section>
    </template>
  </div>
</template>
