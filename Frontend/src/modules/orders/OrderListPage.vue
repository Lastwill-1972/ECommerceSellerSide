<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { listOrders } from '@/modules/merchant/api'
import { useIdentityScope } from '@/modules/workspace/useIdentityScope'
import PageButtons from '@/modules/workspace/components/PageButtons.vue'
import { formatDecimal, formatNaiveDateTime } from '@/shared/http/precision'
import type { IdentityRole, Order, Page } from '@/shared/types/api'

type Status = '' | 'PENDING' | 'COMPLETED' | 'CANCELLED'

const ALLOWED: IdentityRole[] = ['SHOP_OWNER', 'SHOP_EMPLOYEE']
const STATUS_OPTIONS: { value: Status; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待完成' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'CANCELLED', label: '已取消' },
]
const STATUS_LABELS: Record<string, string> = { PENDING: '待完成', COMPLETED: '已完成', CANCELLED: '已取消' }

const status = ref<Status>('')
const page = ref<Page<Order> | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
let sequence = 0

const scope = useIdentityScope(ALLOWED, () => {
  sequence++
  page.value = null
  status.value = ''
  error.value = ''
  notice.value = ''
  loading.value = false
})

const role = computed(() => scope.auth.user?.role ?? null)
const permitted = computed(() => role.value !== null && ALLOWED.includes(role.value))
const prefix = computed(() => (role.value === 'SHOP_EMPLOYEE' ? '/employee' : role.value === 'SHOP_OWNER' ? '/shop' : null))

/**
 * Record-level scope check against the real identity, never trusting server filters.
 * Staff may see every order of their own shop, so `created_by_user_id` is deliberately NOT filtered.
 */
function belongsToScope(order: Order): boolean {
  const user = scope.auth.user
  if (!user) return false
  if (order.merchant_id !== user.merchant_id) return false
  return order.shop_id === user.shop_id
}

async function load(next = 0) {
  if (!permitted.value) return
  const captured = scope.capture()
  const request = ++sequence
  page.value = null
  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    const capturedRole = role.value
    const result = await listOrders(20, next, status.value ? { status: status.value } : {})
    if (!scope.current(captured) || request !== sequence || role.value !== capturedRole) return
    const items = result.items.filter(belongsToScope)
    if (items.length !== result.items.length) notice.value = '部分订单不属于当前账号的商家/店铺范围，已隐藏。'
    page.value = { ...result, items }
  } catch (cause) {
    if (scope.current(captured) && request === sequence) error.value = scope.explain(cause)
  } finally {
    if (scope.current(captured) && request === sequence) loading.value = false
  }
}

function changeStatus(value: Status) {
  if (!permitted.value) return
  status.value = value
  if (value === '') notice.value = ''
  void load(0)
}

watch(status, () => { if (permitted.value) void load(0) })
watch(scope.revision, () => { if (permitted.value) void load(0) }, { flush: 'post' })
onMounted(() => { if (permitted.value) void load(0) })
</script>
<template>
  <div class="merchant-page">
    <div class="welcome-row">
      <div>
        <div class="page-eyebrow">ORDERS</div>
        <h1>订单列表</h1>
        <p>展示当前店铺范围内的全部订单（含同事创建的订单）；金额与编号按原文展示，不做数值转换。</p>
      </div>
      <div>
        <RouterLink v-if="prefix" :to="`${prefix}/orders/create`"><el-button type="primary" :disabled="!permitted || loading">创建订单</el-button></RouterLink>
        <el-button :disabled="!permitted || loading" @click="load(page?.offset ?? 0)">刷新</el-button>
      </div>
    </div>
    <p v-if="!permitted" class="workspace-error" role="alert">当前身份无权查看订单列表，请确认身份后刷新。</p>
    <template v-else>
      <section class="merchant-card">
        <h2>筛选</h2>
        <div class="workspace-tabs">
          <el-button v-for="option in STATUS_OPTIONS" :key="option.value || 'all'" :disabled="loading" :type="status === option.value ? 'primary' : 'default'" @click="changeStatus(option.value)">{{ option.label }}</el-button>
        </div>
        <p v-if="error" class="workspace-error" role="alert">{{ error }} <el-button link :disabled="loading" @click="load(page?.offset ?? 0)">重试</el-button></p>
        <p v-if="notice" class="workspace-warning" role="status">{{ notice }}</p>
      </section>
      <section class="merchant-card">
        <h2>订单</h2>
        <p v-if="loading" class="list-state">正在加载订单…</p>
        <p v-else-if="!page || !page.items.length" class="list-state">暂无订单。</p>
        <div v-else class="table-wrap">
          <table class="merchant-table">
            <thead><tr><th>订单编号</th><th>状态</th><th>金额</th><th>明细数</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="order in page.items" :key="order.id">
                <td>{{ order.id }}</td>
                <td>{{ STATUS_LABELS[order.status] ?? order.status }}</td>
                <td>{{ formatDecimal(order.total_amount) }}</td>
                <td>{{ order.items.length }}</td>
                <td>{{ formatNaiveDateTime(order.created_at) }}</td>
                <td><RouterLink v-if="prefix" :to="`${prefix}/orders/${order.id}`"><el-button link>详情</el-button></RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
        <PageButtons :page="page" :busy="loading" @change="load" />
      </section>
    </template>
  </div>
</template>
