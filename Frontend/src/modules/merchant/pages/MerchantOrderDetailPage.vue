<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { formatDecimal, formatNaiveDateTime } from '@/shared/http/precision'
import { ApiError } from '@/shared/http/errors'
import type { Order } from '@/shared/types/api'
import { getOrder } from '@/modules/merchant/api'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
const route = useRoute(), order = ref<Order | null>(null), loading = ref(false), error = ref(''), notFound = ref(false)
let ticket = 0
const scope = useMerchantScope(() => { ticket += 1; order.value = null; loading.value = false; error.value = ''; notFound.value = false })
async function load() {
  const id = String(route.params.id ?? ''), request = scope.capture(), sequence = ++ticket
  order.value = null; error.value = ''; notFound.value = false; loading.value = true
  try { const value = await getOrder(id); if (scope.current(request) && ticket === sequence) { if (value.merchant_id !== scope.auth.user?.merchant_id) throw new Error('服务器返回的订单不属于当前商家，已隐藏该订单。'); order.value = value } }
  catch (cause) { if (scope.current(request) && ticket === sequence) { if (cause instanceof ApiError && cause.status === 404) { notFound.value = true; error.value = '订单不存在，或当前商家无权查看此订单。' } else { const message = await scope.explain(cause); if (scope.current(request) && ticket === sequence) error.value = message } } }
  finally { if (scope.current(request) && ticket === sequence) loading.value = false }
}
watch(() => route.params.id, () => { void load() })
onMounted(() => { void load() })
</script>
<template><div class="merchant-page"><div class="welcome-row"><div><div class="page-eyebrow">ORDER SNAPSHOT</div><h1>订单详情</h1><p>编号：{{ route.params.id }}</p></div><RouterLink class="back-link" :to="{ name: 'merchant-orders' }">返回订单列表</RouterLink></div>
  <section class="merchant-card"><div v-if="loading" class="list-state">正在读取订单详情…</div><div v-else-if="error" class="list-state state-error" role="alert">{{ error }} <el-button v-if="!notFound" link type="primary" @click="load">重试</el-button><RouterLink v-else :to="{ name: 'merchant-orders' }">返回订单列表</RouterLink></div><template v-else-if="order"><div class="order-facts"><div><small>订单状态</small><strong>{{ order.status }}</strong></div><div><small>店铺编号</small><strong>{{ order.shop_id }}</strong></div><div><small>创建人编号</small><strong>{{ order.created_by_user_id }}</strong></div><div><small>创建时间（服务端本地时间）</small><strong>{{ formatNaiveDateTime(order.created_at) }}</strong></div><div><small>订单总额快照</small><strong>¥{{ formatDecimal(order.total_amount) }}</strong></div></div><h2 class="detail-heading">下单商品快照</h2><div class="table-wrap"><table class="merchant-table"><thead><tr><th>商品名称快照</th><th>SKU</th><th>规格快照</th><th>数量</th><th>单价快照</th><th>小计快照</th></tr></thead><tbody><tr v-for="item in order.items" :key="item.id"><td>{{ item.spu_name_snapshot }}</td><td>{{ item.sku_code_snapshot }} · {{ item.sku_id }}</td><td>{{ item.specification_snapshot }}</td><td>{{ item.quantity }}</td><td>¥{{ formatDecimal(item.unit_price) }}</td><td>¥{{ formatDecimal(item.subtotal) }}</td></tr></tbody></table></div></template></section>
</div></template>
