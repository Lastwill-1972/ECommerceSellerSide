<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { Page, Shop } from '@/shared/types/api'
import { listShops } from '@/modules/merchant/api'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
const page = ref<Page<Shop> | null>(null), loading = ref(false), error = ref(''), offset = ref(0)
const scope = useMerchantScope(() => { page.value = null; error.value = ''; offset.value = 0; loading.value = false })
let sequence = 0
async function load(next = offset.value) {
  const request = scope.capture(), ticket = ++sequence; loading.value = true; error.value = ''; offset.value = next
  try { const value = await listShops(20, next); if (scope.current(request) && ticket === sequence) { if (value.items.some((shop) => shop.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('服务器返回了其他商家的店铺，已隐藏列表。'); page.value = value } }
  catch (reason) { if (scope.current(request) && ticket === sequence) { const message = await scope.explain(reason); if (scope.current(request) && ticket === sequence) error.value = message } }
  finally { if (scope.current(request) && ticket === sequence) loading.value = false }
}
onMounted(() => { void load(0) })
</script>
<template>
  <div class="merchant-page">
    <div class="welcome-row"><div><div class="page-eyebrow">SHOPS</div><h1>店铺列表</h1><p>只显示当前商家名下由服务端返回的店铺。</p></div><el-button class="refresh-button" :loading="loading" @click="load()">刷新</el-button></div>
    <section class="merchant-card">
      <div v-if="loading" class="list-state">正在读取店铺…</div><div v-else-if="error" class="list-state state-error" role="alert">{{ error }} <el-button link type="primary" @click="load()">重试</el-button></div><template v-else-if="page?.items.length"><div class="table-wrap"><table class="merchant-table"><thead><tr><th>店铺</th><th>店铺编号</th><th>店主编号</th><th>创建时间</th></tr></thead><tbody><tr v-for="shop in page.items" :key="shop.id"><td>{{ shop.name }}</td><td>{{ shop.id }}</td><td>{{ shop.owner_user_id }}</td><td>{{ formatNaiveDateTime(shop.created_at) }}</td></tr></tbody></table></div></template><div v-else class="list-state">{{ page ? '当前商家还没有店铺。' : '尚无数据。' }}</div>
      <div v-if="page && page.total" class="pager"><span>共 {{ page.total }} 家 · 第 {{ Math.floor(page.offset / page.limit) + 1 }} 页</span><div><el-button size="small" :disabled="loading || page.offset === 0" @click="load(Math.max(0, page.offset - page.limit))">上一页</el-button><el-button size="small" :disabled="loading || page.offset + page.limit >= page.total" @click="load(page.offset + page.limit)">下一页</el-button></div></div>
    </section>
  </div>
</template>
