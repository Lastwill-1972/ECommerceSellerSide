<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElAlert, ElDialog, ElMessage, ElOption, ElSelect } from 'element-plus'
import { ApiError } from '@/shared/http/errors'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { ApplicationDecision, Page, Shop, ShopApplication, WarehouseAccessApplication } from '@/shared/types/api'
import { listShopApplications, listShops, listWarehouseApplications, reviewShopApplication, reviewWarehouseApplication } from '@/modules/merchant/api'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'

const props = defineProps<{ kind: 'shop' | 'warehouse' }>()
type ReviewItem = ShopApplication | WarehouseAccessApplication
const data = ref<Page<ReviewItem> | null>(null), shops = ref<Shop[]>([]), shopTotal = ref(0), loading = ref(false), busy = ref(false), error = ref('')
const offset = ref(0), statusFilter = ref(''), selected = ref<ReviewItem | null>(null), decision = ref<ApplicationDecision>('APPROVE'), reason = ref(''), modalError = ref(''), closing = ref(false)
const dialogOpen = computed({ get: () => selected.value !== null, set: (open: boolean) => { if (!open && !busy.value) selected.value = null } })
let sequence = 0
const scope = useMerchantScope(() => { sequence += 1; data.value = null; shops.value = []; shopTotal.value = 0; error.value = ''; offset.value = 0; selected.value = null; reason.value = ''; modalError.value = ''; busy.value = false; loading.value = false; closing.value = false })
watch(selected, (value, previous) => { if (previous && !value) closing.value = true })
const isShop = computed(() => props.kind === 'shop')
const shopNames = computed(() => new Map(shops.value.map((shop) => [shop.id, shop.name])))
async function load(next = offset.value) {
  const request = scope.capture(), ticket = ++sequence
  loading.value = true; error.value = ''; offset.value = next
  try {
    const [result, shopPage] = await Promise.all([
      isShop.value ? listShopApplications(20, next, statusFilter.value || undefined) : listWarehouseApplications(20, next, statusFilter.value || undefined),
      listShops(100, 0),
    ])
    if (scope.current(request) && ticket === sequence) {
      const items = result.items as ReviewItem[]
      if (props.kind === 'shop' && items.some((item) => (item as ShopApplication).merchant_id !== scope.auth.user?.merchant_id)) throw new Error('服务器返回了其他商家的申请，已隐藏列表。')
      if (shopPage?.items.some((shop) => shop.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('服务器返回了其他商家的店铺，已隐藏列表。')
      data.value = result as Page<ReviewItem>; if (shopPage) { shops.value = shopPage.items; shopTotal.value = shopPage.total }
    }
  } catch (cause) { if (scope.current(request) && ticket === sequence) { const message = await scope.explain(cause); if (scope.current(request) && ticket === sequence) error.value = message } }
  finally { if (scope.current(request) && ticket === sequence) loading.value = false }
}
function openReview(item: ReviewItem, choice: ApplicationDecision) { if (busy.value || closing.value || item.status !== 'PENDING') return; selected.value = item; decision.value = choice; reason.value = ''; modalError.value = '' }
const normalizedReason = computed(() => reason.value.trim())
const reasonLength = computed(() => Array.from(normalizedReason.value).length)
const reasonInvalid = computed(() => decision.value === 'REJECT' && (!normalizedReason.value || reasonLength.value > 2000))
async function submitReview() {
  if (!selected.value || busy.value || reasonInvalid.value) return
  const chosen = selected.value, choice = decision.value, text = normalizedReason.value, request = scope.capture()
  busy.value = true; modalError.value = ''
  try {
    if (isShop.value) await reviewShopApplication(chosen.id, choice, choice === 'REJECT' ? text : undefined)
    else await reviewWarehouseApplication(chosen.id, choice, choice === 'REJECT' ? text : undefined)
    if (!scope.current(request)) return
    selected.value = null; ElMessage.success('审批结果已提交。')
    await load(offset.value)
  } catch (cause) {
    if (!scope.current(request)) return
    if (cause instanceof ApiError && cause.status === 409) {
      selected.value = null
      ElMessage.error('申请状态已变化，列表正在刷新。请检查当前状态后再决定。')
      await load(offset.value)
    } else if (cause instanceof ApiError && cause.status === 422) {
      const fieldMessage = cause.fields.rejection_reason?.join(' ') ?? cause.message
      modalError.value = fieldMessage
    } else { const message = await scope.explain(cause); if (scope.current(request)) modalError.value = message }
  } finally { if (scope.current(request)) busy.value = false }
}
onMounted(() => { void load(0) })
</script>

<template>
  <div class="merchant-page">
    <div class="welcome-row"><div><div class="page-eyebrow">REVIEW CENTER <span></span> 审批中心</div><h1>{{ isShop ? '店铺申请' : '共享仓库授权申请' }}</h1><p>{{ isShop ? '审核申请后，服务端会创建店铺并记录结果。' : '审核店铺使用共享仓库的资格申请。' }}</p></div><el-button class="refresh-button" :loading="loading" @click="load()">刷新</el-button></div>
    <section class="merchant-card">
      <div v-if="isShop" class="queue-shop-count">当前商家店铺共 {{ shopTotal }} 家（API 同步）</div><div class="list-toolbar"><label>申请状态 <el-select v-model="statusFilter" clearable placeholder="全部状态" @change="load(0)"><el-option label="待处理" value="PENDING" /><el-option label="已批准" value="APPROVED" /><el-option label="已拒绝" value="REJECTED" /></el-select></label></div>
      <div v-if="loading" class="list-state">正在读取申请…</div><div v-else-if="error" class="list-state state-error" role="alert">{{ error }} <el-button link type="primary" @click="load()">重试</el-button></div>
      <div v-else-if="data?.items.length" class="table-wrap"><table class="merchant-table"><thead><tr><th>申请编号</th><th>{{ isShop ? '店铺名称' : '申请店铺' }}</th><th>申请人编号</th><th>状态</th><th>申请时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in data.items" :key="item.id"><td>{{ item.id }}</td><td>{{ isShop ? (item as ShopApplication).shop_name : (shopNames.get((item as WarehouseAccessApplication).shop_id) ? `${shopNames.get((item as WarehouseAccessApplication).shop_id)} · ${(item as WarehouseAccessApplication).shop_id}` : `店铺编号 ${(item as WarehouseAccessApplication).shop_id}`) }}</td><td>{{ item.applicant_user_id }}</td><td><span class="status-pill" :class="`status-${item.status.toLowerCase()}`">{{ item.status === 'PENDING' ? '待处理' : item.status === 'APPROVED' ? '已批准' : '已拒绝' }}</span></td><td>{{ formatNaiveDateTime(item.created_at) }}</td><td><div v-if="item.status === 'PENDING'" class="review-actions"><el-button size="small" type="primary" :disabled="busy || closing" @click="openReview(item, 'APPROVE')">批准</el-button><el-button size="small" :disabled="busy || closing" @click="openReview(item, 'REJECT')">拒绝</el-button></div><span v-else>—</span></td></tr></tbody></table></div>
      <div v-else class="list-state">{{ data ? '没有符合筛选条件的申请。' : '尚无数据。' }}</div>
      <div v-if="data && data.total > 0" class="pager"><span>共 {{ data.total }} 项 · 第 {{ Math.floor(data.offset / data.limit) + 1 }} 页</span><div><el-button size="small" :disabled="loading || !data.offset" @click="load(Math.max(0, data.offset - data.limit))">上一页</el-button><el-button size="small" :disabled="loading || data.offset + data.limit >= data.total" @click="load(data.offset + data.limit)">下一页</el-button></div></div>
    </section>
    <el-dialog v-model="dialogOpen" :title="decision === 'APPROVE' ? '确认批准申请' : '拒绝申请'" width="min(520px, 92vw)" :show-close="!busy" :close-on-click-modal="false" :close-on-press-escape="false" @closed="closing = false; modalError = ''; reason = ''">
      <p v-if="selected">申请编号：{{ selected.id }}</p>
      <el-alert v-if="!isShop && decision === 'APPROVE'" title="批准代表店铺获得共享仓库使用资格，不等于任意库存修改权。" type="warning" :closable="false" show-icon />
      <template v-if="decision === 'REJECT'"><label class="dialog-label">拒绝原因（必填，最多 2000 个 Unicode 字符）</label><el-input v-model="reason" type="textarea" :rows="4" placeholder="请输入原因" /><small>{{ reasonLength }} / 2000 · 按 Unicode 码点计数；首尾空白会被移除。</small></template>
      <p v-if="reasonInvalid" class="inline-error" role="alert">请输入有效原因，去除首尾空白后最多 2000 个 Unicode 字符。</p><p v-if="modalError" class="inline-error" role="alert">{{ modalError }}</p>
      <template #footer><el-button :disabled="busy" @click="selected = null">取消</el-button><el-button type="primary" :loading="busy" :disabled="busy || reasonInvalid" @click="submitReview">确认{{ decision === 'APPROVE' ? '批准' : '拒绝' }}</el-button></template>
    </el-dialog>
  </div>
</template>
