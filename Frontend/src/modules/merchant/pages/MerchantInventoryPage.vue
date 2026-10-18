<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElAlert, ElCard, ElEmpty, ElForm, ElFormItem, ElMessage, ElOption, ElPagination, ElSelect, ElSkeleton, ElTable, ElTableColumn } from 'element-plus'
import { ApiError } from '@/shared/http/errors'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
import * as api from '@/modules/merchant/inventoryApi'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { Page, Stock, StockMovement, Warehouse } from '@/shared/types/api'

interface PendingOperation { key: string; sku_id: string; delta: number; state: 'unknown' | 'confirmed'; result?: api.StockOperationResult; createdAt: string }
const warehouse = ref<Warehouse | null>(null), stocks = ref<Page<Stock> | null>(null), movements = ref<Page<StockMovement> | null>(null)
const stockOffset = ref(0), movementOffset = ref(0), busy = ref(false), loading = ref(false), error = ref('')
const pending = ref<PendingOperation | null>(null), confirmation = ref(''), form = reactive({ sku_id: '', delta: '' })
let sequence = 0, activeStorageKey = ''
const scope = useMerchantScope(() => {
  sequence += 1; stocks.value = null; movements.value = null; warehouse.value = null; pending.value = null
  confirmation.value = ''; form.sku_id = ''; form.delta = ''; error.value = ''; activeStorageKey = ''
  stockOffset.value = 0; movementOffset.value = 0; busy.value = false; loading.value = false
})
const mayOperate = computed(() => !pending.value && !busy.value)
function storageKey() { const user = scope.auth.user; return user ? `seller_inventory_pending:${user.id}:${user.merchant_id ?? 'none'}` : '' }
function persist(value: PendingOperation | null): boolean {
  if (!activeStorageKey) return false
  try {
    if (value) sessionStorage.setItem(activeStorageKey, JSON.stringify({ key: value.key, sku_id: value.sku_id, delta: value.delta, state: value.state, ...(value.result ? { result: value.result } : {}), createdAt: value.createdAt }))
    else sessionStorage.removeItem(activeStorageKey)
    pending.value = value
    return true
  } catch {
    if (value) pending.value = value
    return false
  }
}
function clearCapturedOperation(namespace: string, operationKey: string): boolean {
  if (!namespace) return false
  try {
    const raw = sessionStorage.getItem(namespace)
    if (raw) {
      const stored = JSON.parse(raw) as Record<string, unknown>
      if (stored.key !== operationKey) return false
      sessionStorage.removeItem(namespace)
    }
    if (activeStorageKey === namespace && pending.value?.key === operationKey) pending.value = null
    return true
  } catch { return false }
}
function restorePending() {
  const nextKey = storageKey()
  if (!nextKey || nextKey === activeStorageKey) return
  activeStorageKey = nextKey
  try {
    const raw = sessionStorage.getItem(activeStorageKey)
    if (!raw) return
    const value = JSON.parse(raw) as Record<string, unknown>
    const allowedFields = new Set(['key', 'sku_id', 'delta', 'state', 'result', 'createdAt'])
    if (Object.keys(value).some((field) => !allowedFields.has(field))) { sessionStorage.removeItem(activeStorageKey); return }
    const uuid = typeof value.key === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.key)
    if (uuid && typeof value.sku_id === 'string' && /^[1-9]\d*$/.test(value.sku_id) && Number.isSafeInteger(value.delta) && value.delta !== 0 && (value.state === 'unknown' || value.state === 'confirmed') && typeof value.createdAt === 'string') {
      const restored: PendingOperation = { key: value.key as string, sku_id: value.sku_id, delta: value.delta as number, state: value.state, createdAt: value.createdAt }
      if (value.state === 'confirmed' && typeof value.result === 'object' && value.result !== null) {
        const result = value.result as Record<string, unknown>
        const resultFields = new Set(['movement_id', 'sku_id', 'quantity_before', 'quantity_delta', 'quantity_after', 'idempotency_key'])
        const integerFields = ['quantity_before', 'quantity_delta', 'quantity_after']
        if (Object.keys(result).every((field) => resultFields.has(field)) && result.idempotency_key === restored.key && typeof result.movement_id === 'string' && /^\d+$/.test(result.movement_id) && result.sku_id === restored.sku_id && integerFields.every((field) => typeof result[field] === 'string' && /^-?\d+$/.test(result[field] as string))) restored.result = result as unknown as api.StockOperationResult
        else restored.state = 'unknown'
      }
      pending.value = restored
    }
    else sessionStorage.removeItem(activeStorageKey)
  } catch { pending.value = null }
}
async function load() {
  restorePending()
  const request = ++sequence, captured = scope.capture(); loading.value = true; error.value = ''
  try {
    const [warehouseResult, stockPage, movementPage] = await Promise.all([
      api.getWarehouse(), api.listStocks(20, stockOffset.value), api.listMovements(20, movementOffset.value),
    ])
    if (!scope.current(captured) || request !== sequence) return
    if (warehouseResult.merchant_id !== scope.auth.user?.merchant_id || stockPage.items.some((item) => item.warehouse_id !== warehouseResult.id) || movementPage.items.some((item) => item.merchant_id !== warehouseResult.merchant_id || item.warehouse_id !== warehouseResult.id)) throw new Error('服务器返回了其他商家的仓库或库存数据。')
    warehouse.value = warehouseResult; stocks.value = stockPage; movements.value = movementPage
  } catch (cause) { if (!scope.current(captured) || request !== sequence) return; const message = await scope.explain(cause); if (scope.current(captured) && request === sequence) error.value = message }
  finally { if (scope.current(captured) && request === sequence) loading.value = false }
}
function changePage(which: 'stock' | 'movement', offset: number) {
  if (which === 'stock') stockOffset.value = offset
  else movementOffset.value = offset
  void load()
}
watch(scope.revision, () => { restorePending(); if (scope.auth.user?.merchant_id) void load() }, { flush: 'post' })
onMounted(() => { restorePending(); void load() })

function newOperation() {
  if (busy.value || pending.value || scope.auth.user?.role !== 'MERCHANT_ADMIN') return
  const sku = form.sku_id.trim(), raw = form.delta.trim()
  if (!/^[1-9]\d*$/.test(sku)) { ElMessage.error('请选择有效 SKU。'); return }
  if (!/^-?(?:0|[1-9]\d*)$/.test(raw)) { ElMessage.error('库存变化必须是整数。'); return }
  const delta = Number(raw)
  if (!Number.isSafeInteger(delta) || delta === 0) { ElMessage.error('库存变化必须是非零安全整数。'); return }
  const stock = stocks.value?.items.find((item) => item.sku_id === sku)
  if (stock && BigInt(stock.quantity) + BigInt(delta) < 0n) { ElMessage.error('库存变化不能使库存低于零。'); return }
  const operation = { key: crypto.randomUUID(), sku_id: sku, delta, state: 'unknown' as const, createdAt: new Date().toISOString() }
  if (!persist(operation)) { confirmation.value = '浏览器会话存储不可用，未发送库存请求。启用会话存储后再试，避免无法保存未知结果的幂等键。'; ElMessage.error(confirmation.value); return }
  void send(operation, false)
}
function isUnknown(cause: unknown) {
  if (!(cause instanceof ApiError)) return true
  return cause.status === undefined || cause.status >= 500
}
async function send(operation: PendingOperation, explicitRetry: boolean) {
  if (busy.value) return
  busy.value = true; confirmation.value = ''; const captured = scope.capture(), capturedStorageKey = activeStorageKey
  // Store uncertainty before transmission so a page reload cannot lose a possibly committed operation.
  if (!persist({ ...operation, state: 'unknown' })) {
    confirmation.value = '浏览器会话存储不可用，未发送请求。启用会话存储后再明确重试，确保幂等键可恢复。'
    busy.value = false
    return
  }
  try {
    const result = await api.adjustStock(operation.sku_id, operation.delta, operation.key)
    if (!scope.current(captured)) return
    persist({ ...operation, state: 'confirmed', result })
    confirmation.value = `操作已确认，流水编号 ${result.movement_id}，操作后库存 ${result.quantity_after}。`
    if (explicitRetry) ElMessage.success('已用原幂等键确认操作结果。')
    await load()
  } catch (cause) {
    if (!scope.current(captured)) return
    if (isUnknown(cause)) {
      persist({ ...operation, state: 'unknown' })
      confirmation.value = cause instanceof ApiError && cause.status === 503
        ? '服务返回 503，请求结果未知。服务端可能已经提交，请先核对库存和流水；明确重试会复用同一个幂等键。'
        : '请求结果未知。服务端可能已经提交，请先核对库存和流水；明确重试会复用同一个幂等键。'
    } else {
      const cleared = clearCapturedOperation(capturedStorageKey, operation.key)
      const message = await scope.explain(cause)
      if (!scope.current(captured)) return
      confirmation.value = `操作未成功：${message} 可以修正后创建一笔新操作。`
      if (!cleared) confirmation.value += ' 会话存储无法清除旧记录，仍将保留并锁住库存表单。'
      if (cause instanceof ApiError && [401, 403, 404, 409, 422, 503].includes(cause.status ?? 0)) ElMessage.error(confirmation.value)
      await load()
    }
  } finally { if (scope.current(captured)) busy.value = false }
}
async function verifyUnknown() {
  const operation = pending.value
  if (!operation || operation.state !== 'unknown' || busy.value) return
  busy.value = true; confirmation.value = ''; const captured = scope.capture()
  try {
    const [warehouseResult, stockPage, movementPage] = await Promise.all([
      api.getWarehouse(), api.listStocks(100, 0), api.listMovements(100, 0, operation.sku_id),
    ])
    if (!scope.current(captured)) return
    if (warehouseResult.merchant_id !== scope.auth.user?.merchant_id || movementPage.items.some((item) => item.merchant_id !== scope.auth.user?.merchant_id || item.warehouse_id !== warehouseResult.id) || stockPage.items.some((item) => item.warehouse_id !== warehouseResult.id)) throw new Error('服务器返回了其他商家的库存核对数据。')
    const match = movementPage.items.find((item) => item.idempotency_key === operation.key)
    if (match) {
      if (match.sku_id !== operation.sku_id || match.quantity_delta !== String(operation.delta) || match.actor_user_id !== scope.auth.user?.id || match.merchant_id !== scope.auth.user?.merchant_id) {
        confirmation.value = '流水中存在相同幂等键但请求参数或身份不匹配，仍保留为未知状态；请联系管理员核查。'
      } else {
        persist({ ...operation, state: 'confirmed', result: { movement_id: match.movement_id, sku_id: match.sku_id, quantity_before: match.quantity_before, quantity_delta: match.quantity_delta, quantity_after: match.quantity_after, idempotency_key: operation.key } })
        confirmation.value = `已在流水中找到此幂等键：流水 ${match.movement_id}，操作后库存 ${match.quantity_after}。`
      }
    } else {
      const stock = stockPage.items.find((item) => item.sku_id === operation.sku_id)
      confirmation.value = `已刷新库存${stock ? `（当前 ${stock.quantity}）` : ''}与最近 100 条该 SKU 流水，暂未找到此幂等键；结果仍未知，请勿另建相同操作。`
    }
    await load()
  } catch (cause) { if (!scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) confirmation.value = message }
  finally { if (scope.current(captured)) busy.value = false }
}
function abandonResolved() {
  if (pending.value?.state === 'confirmed') { persist(null); confirmation.value = '已确认的操作记录已收起。' }
}
function formatInteger(value: string) { return BigInt(value).toLocaleString('zh-CN') }
</script>

<template>
  <div class="merchant-page inventory-page">
    <div class="merchant-page-heading"><div><span class="section-kicker">INVENTORY</span><h1>库存管理</h1><p>查看商家仓库、SKU 库存与操作流水，按整数调整库存数量。</p></div><el-button :loading="loading" @click="load">刷新数据</el-button></div>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error"><template #default><el-button text @click="load">重试</el-button></template></el-alert>
    <el-card class="warehouse-summary merchant-card"><div class="warehouse-mark">仓</div><div><span class="section-kicker">当前仓库</span><strong>{{ warehouse?.name ?? (loading ? '正在读取…' : '仓库暂不可用') }}</strong><small v-if="warehouse">仓库编号 {{ warehouse.id }} · 当前商家 {{ warehouse.merchant_id }}</small></div></el-card>
    <el-card class="merchant-card"><template #header><div class="card-heading"><strong>SKU 库存</strong><span class="muted-note">库存为精确整数</span></div></template><el-skeleton v-if="loading && !stocks" :rows="4" animated /><el-empty v-else-if="!stocks?.items.length" description="暂无 SKU 库存" /><el-table v-else :data="stocks.items" row-key="sku_id"><el-table-column prop="spu_name" label="商品" min-width="150" /><el-table-column prop="sku_code" label="SKU 编码" min-width="130" /><el-table-column prop="specification" label="规格" min-width="140" /><el-table-column label="库存数量" width="150"><template #default="{ row }"><strong>{{ formatInteger(row.quantity) }}</strong></template></el-table-column><el-table-column label="操作" width="150"><template #default="{ row }"><el-button link type="primary" :disabled="!mayOperate" @click="form.sku_id = row.sku_id; form.delta = ''">选择调整</el-button></template></el-table-column></el-table><div v-if="stocks && stocks.total > 20" class="pagination-row"><el-pagination background layout="total, prev, pager, next" :page-size="20" :current-page="Math.floor(stockOffset / 20) + 1" :total="stocks.total" @current-change="(page: number) => changePage('stock', (page - 1) * 20)" /></div></el-card>
    <el-card class="merchant-card operation-card">
      <template #header><div class="card-heading"><strong>人工库存调整</strong><span class="muted-note">非零安全整数；正数增加，负数减少</span></div></template><el-form class="operation-form" @submit.prevent="newOperation"><el-form-item label="SKU"><el-select v-model="form.sku_id" placeholder="选择 SKU" :disabled="!mayOperate"><el-option v-for="item in stocks?.items ?? []" :key="item.sku_id" :label="`${item.spu_name} · ${item.sku_code}（现有 ${item.quantity}）`" :value="item.sku_id" /></el-select></el-form-item><el-form-item label="库存变化"><el-input v-model="form.delta" inputmode="numeric" placeholder="例如 10 或 -3" :disabled="!mayOperate" /></el-form-item><el-button type="primary" :loading="busy" :disabled="!mayOperate || !warehouse" @click="newOperation">提交调整</el-button></el-form>
      <el-alert v-if="pending?.state === 'unknown'" type="warning" :closable="false" show-icon title="上一笔库存操作结果未知，当前已锁住新操作。"><p>SKU {{ pending.sku_id }} · 变化 {{ pending.delta }} · 幂等键 {{ pending.key }}</p><p>离开页面或刷新后会在本账号和商家范围内保留此记录，不保存登录凭据。</p><p v-if="confirmation">{{ confirmation }}</p><div class="pending-actions"><el-button :loading="busy" @click="verifyUnknown">核对库存和流水</el-button><el-button type="warning" :loading="busy" @click="send(pending, true)">我已确认，使用同一幂等键重试</el-button></div></el-alert>
      <el-alert v-else-if="pending?.state === 'confirmed'" type="success" :closable="false" show-icon title="最近一笔库存操作已确认。"><p>{{ confirmation || `流水 ${pending.result?.movement_id} · 数量变化 ${pending.delta} · 操作后库存 ${pending.result?.quantity_after}` }}</p><el-button text @click="abandonResolved">收起记录</el-button></el-alert>
      <el-alert v-if="confirmation && !pending" type="info" :closable="false" :title="confirmation" />
    </el-card>
    <el-card class="merchant-card"><template #header><div class="card-heading"><strong>库存操作流水</strong><span class="muted-note">人工操作的幂等键与订单明细编号允许为空</span></div></template><el-skeleton v-if="loading && !movements" :rows="4" animated /><el-empty v-else-if="!movements?.items.length" description="暂无库存流水" /><el-table v-else :data="movements.items" row-key="movement_id"><el-table-column prop="movement_id" label="流水编号" min-width="150" /><el-table-column prop="sku_id" label="SKU 编号" min-width="150" /><el-table-column label="类型" width="140"><template #default="{ row }">{{ row.operation_type === 'MANUAL_ADJUSTMENT' ? '人工调整' : row.operation_type }}</template></el-table-column><el-table-column label="变化" width="110"><template #default="{ row }">{{ row.quantity_delta }}</template></el-table-column><el-table-column label="操作后" width="110"><template #default="{ row }">{{ row.quantity_after }}</template></el-table-column><el-table-column label="幂等键" min-width="250"><template #default="{ row }">{{ row.idempotency_key ?? '—（订单流水）' }}</template></el-table-column><el-table-column label="订单明细" min-width="150"><template #default="{ row }">{{ row.order_item_id ?? '—（人工操作）' }}</template></el-table-column><el-table-column label="时间" min-width="180"><template #default="{ row }">{{ formatNaiveDateTime(row.created_at) }}</template></el-table-column></el-table><div v-if="movements && movements.total > 20" class="pagination-row"><el-pagination background layout="total, prev, pager, next" :page-size="20" :current-page="Math.floor(movementOffset / 20) + 1" :total="movements.total" @current-change="(page: number) => changePage('movement', (page - 1) * 20)" /></div></el-card>
  </div>
</template>
