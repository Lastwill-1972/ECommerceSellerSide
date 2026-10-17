<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { formatDecimal, formatNaiveDateTime } from '@/shared/http/precision'
import type { Order, Page, Shop } from '@/shared/types/api'
import { listOrders, listShops } from '@/modules/merchant/api'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
const data = ref<Page<Order> | null>(null), shops = ref<Shop[]>([]), loading = ref(false), error = ref('')
const offset = ref(0), statusFilter = ref(''), shopFilter = ref('')
const scope = useMerchantScope(() => { data.value = null; shops.value = []; error.value = ''; offset.value = 0; loading.value = false })
let sequence = 0
async function load(next = offset.value) {
  const request = scope.capture(), ticket = ++sequence; loading.value = true; error.value = ''; offset.value = next
  try {
    const [orders, shopPage] = await Promise.all([
      listOrders(20, next, { status: statusFilter.value || undefined, shop_id: shopFilter.value || undefined }),
      listShops(100, 0),
    ])
    if (scope.current(request) && ticket === sequence) {
      if (orders.items.some((order) => order.merchant_id !== scope.auth.user?.merchant_id) || shopPage.items.some((shop) => shop.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('服务器返回了其他商家的订单或店铺，已隐藏列表。')
      data.value = orders; shops.value = shopPage.items
    }
  } catch (cause) { if (scope.current(request) && ticket === sequence) { const message = await scope.explain(cause); if (scope.current(request) && ticket === sequence) error.value = message } }
  finally { if (scope.current(request) && ticket === sequence) loading.value = false }
}
function setStatus(value: string) { statusFilter.value = value; void load(0) }
function setShop(value: string) { shopFilter.value = value; void load(0) }
const shopNames = () => new Map(shops.value.map((shop) => [shop.id, shop.name]))
onMounted(() => { void load(0) })
</script>
<template><div class="merchant-page"><div class="welcome-row"><div><div class="page-eyebrow">ORDERS · READ ONLY</div><h1>订单列表</h1><p>订单及商品金额均使用下单时保存的历史快照。</p></div><el-button class="refresh-button" :loading="loading" @click="load()">刷新</el-button></div>
  <section class="merchant-card"><div class="list-toolbar"><label>状态 <el-select :model-value="statusFilter" clearable placeholder="全部状态" @change="setStatus"><el-option label="待处理" value="PENDING"/><el-option label="已完成" value="COMPLETED"/><el-option label="已取消" value="CANCELLED"/></el-select></label><label>店铺 <el-select :model-value="shopFilter" clearable filterable placeholder="全部店铺" @change="setShop"><el-option v-for="shop in shops" :key="shop.id" :label="shop.name" :value="shop.id"/></el-select></label></div>
    <div v-if="loading" class="list-state">正在读取订单…</div><div v-else-if="error" class="list-state state-error" role="alert">{{ error }} <el-button link type="primary" @click="load()">重试</el-button></div><div v-else-if="data?.items.length" class="table-wrap"><table class="merchant-table"><thead><tr><th>订单编号</th><th>店铺</th><th>状态</th><th>金额快照</th><th>创建时间</th><th></th></tr></thead><tbody><tr v-for="order in data.items" :key="order.id"><td><RouterLink :to="{ name: 'merchant-order-detail', params: { id: order.id } }">{{ order.id }}</RouterLink></td><td>{{ shopNames().get(order.shop_id) ?? `店铺编号 ${order.shop_id}` }}</td><td><span class="status-pill">{{ order.status === 'PENDING' ? '待处理' : order.status === 'COMPLETED' ? '已完成' : '已取消' }}</span></td><td>¥{{ formatDecimal(order.total_amount) }}</td><td>{{ formatNaiveDateTime(order.created_at) }}</td><td><RouterLink :to="{ name: 'merchant-order-detail', params: { id: order.id } }">查看详情</RouterLink></td></tr></tbody></table></div><div v-else class="list-state">{{ data ? '没有符合条件的订单。' : '尚无数据。' }}</div>
    <div v-if="data && data.total > 0" class="pager"><span>共 {{ data.total }} 单 · 第 {{ Math.floor(data.offset / data.limit) + 1 }} 页</span><div><el-button size="small" :disabled="loading || !data.offset" @click="load(Math.max(0, data.offset - data.limit))">上一页</el-button><el-button size="small" :disabled="loading || data.offset + data.limit >= data.total" @click="load(data.offset + data.limit)">下一页</el-button></div></div>
  </section>
</div></template>
