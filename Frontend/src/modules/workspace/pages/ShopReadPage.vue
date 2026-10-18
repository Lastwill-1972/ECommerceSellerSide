<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElDialog } from 'element-plus'
import { ApiError } from '@/shared/http/errors'
import { listShops, listWarehouseApplications } from '@/modules/merchant/api'
import { getWarehouse, listStocks } from '@/modules/merchant/inventoryApi'
import { listCategories, listSpus, getSpu } from '@/modules/merchant/catalogApi'
import { applyWarehouse, statusName } from '../api'
import { useIdentityScope } from '../useIdentityScope'
import PageButtons from '../components/PageButtons.vue'
import { formatDecimal, formatNaiveDateTime } from '@/shared/http/precision'
import type { Page, Shop, Warehouse, Stock, WarehouseAccessApplication, Category, Spu, SpuDetail } from '@/shared/types/api'
const route = useRoute()
const mode = computed(() => route.name === 'shop-home' || route.name === 'employee-home' ? 'home' : route.name === 'shop-warehouse' ? 'warehouse' : route.name === 'shop-catalog' || route.name === 'employee-catalog' ? 'catalog' : 'inventory')
const title = computed(() => ({ home: '店铺概览', warehouse: '仓库授权申请', catalog: '商家商品（只读）', inventory: '共享库存（只读）' })[mode.value])
const shop = ref<Shop | null>(null), warehouse = ref<Warehouse | null>(null), stocks = ref<Page<Stock> | null>(null), applications = ref<Page<WarehouseAccessApplication> | null>(null), categories = ref<Page<Category> | null>(null), products = ref<Page<Spu> | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), denied = ref(false), offset = ref(0), categoryOffset = ref(0)
const detail = ref<SpuDetail | null>(null), detailOpen = ref(false), detailLoading = ref(false), detailError = ref(''), detailId = ref('')
let sequence = 0, detailSequence = 0
const isOwner = computed(() => scope.auth.user?.role === 'SHOP_OWNER')
const isEmployee = computed(() => scope.auth.user?.role === 'SHOP_EMPLOYEE')
const scope = useIdentityScope(['SHOP_OWNER', 'SHOP_EMPLOYEE'], () => { sequence++; detailSequence++; shop.value = null; warehouse.value = null; stocks.value = null; applications.value = null; categories.value = null; products.value = null; detail.value = null; detailOpen.value = false; detailId.value = ''; detailError.value = ''; detailLoading.value = false; error.value = ''; notice.value = ''; denied.value = false; offset.value = 0; categoryOffset.value = 0; loading.value = false; busy.value = false })
async function load(next = offset.value) {
  const captured = scope.capture(), ticket = ++sequence
  // Do not clear notice/busy here: that would erase the success message of a just submitted
  // warehouse application and release the write lock early. Scope reset still clears both.
  shop.value = null; warehouse.value = null; stocks.value = null; applications.value = null; categories.value = null; products.value = null
  loading.value = true; error.value = ''; denied.value = false; offset.value = next
  try {
    if (mode.value === 'home') {
      // Employees never call GET /shops; shop identity comes from the authenticated identity only.
      if (isEmployee.value) {
        if (!scope.auth.user?.shop_id || !scope.auth.user?.merchant_id) throw new Error('当前员工身份未绑定店铺，无法读取店铺概览。')
        if (!scope.current(captured) || ticket !== sequence) return
        // No shop object is fabricated for employees: the employee view renders from `me` only.
        return
      }
      const result = await listShops(20, 0); if (!scope.current(captured) || ticket !== sequence) return
      if (result.items.some(row => row.id !== scope.auth.user?.shop_id || row.owner_user_id !== scope.auth.user?.id || row.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('返回的店铺不属于当前负责人。')
      shop.value = result.items[0] ?? null
    } else if (mode.value === 'warehouse') {
      if (!isOwner.value) throw new Error('员工无权提交或查看仓库授权申请，请联系本店负责人。')
      const result = await listWarehouseApplications(20, next); if (!scope.current(captured) || ticket !== sequence) return
      if (result.items.some(row => row.shop_id !== scope.auth.user?.shop_id)) throw new Error('其他店铺的仓库申请已隐藏。')
      applications.value = result
    } else if (mode.value === 'catalog') {
      // Read-only shared catalog for the current merchant; both owner and employee can browse.
      const [cats, spus] = await Promise.all([listCategories(20, categoryOffset.value), listSpus(20, next)])
      if (!scope.current(captured) || ticket !== sequence) return
      if ([...cats.items, ...spus.items].some(row => row.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('其他商家的商品已隐藏。')
      categories.value = cats; products.value = spus
    } else {
      const [wh, stock] = await Promise.all([getWarehouse(), listStocks(20, next)])
      if (!scope.current(captured) || ticket !== sequence) return
      if (wh.merchant_id !== scope.auth.user?.merchant_id || stock.items.some(row => row.warehouse_id !== wh.id)) throw new Error('其他商家的仓库或库存已隐藏。')
      warehouse.value = wh; stocks.value = stock
    }
  } catch (cause) {
    if (scope.current(captured) && ticket === sequence) {
      denied.value = cause instanceof ApiError && cause.status === 403
      if (denied.value && mode.value === 'inventory' && isEmployee.value) {
        error.value = '本店未向你授权共享仓库库存，需由商家管理员审批店主提交的仓库使用申请后才能读取，请联系本店负责人。'
      } else error.value = scope.explain(cause)
    }
  }
  finally { if (scope.current(captured) && ticket === sequence) loading.value = false }
}
async function submit() {
  if (busy.value || !isOwner.value || mode.value !== 'warehouse') return
  const captured = scope.capture(); busy.value = true; error.value = ''; notice.value = ''
  try { const result = await applyWarehouse(); if (!scope.current(captured)) return; notice.value = `仓库申请已提交：${statusName(result.status)}，批准前不会获得库存访问权。`; await load(0) }
  catch (cause) { if (scope.current(captured)) error.value = scope.explain(cause) }
  finally { if (scope.current(captured)) busy.value = false }
}
async function openDetail(id: string) {
  if (mode.value !== 'catalog') return
  const captured = scope.capture(), ticket = ++detailSequence; detailId.value = id; detailOpen.value = true; detailLoading.value = true; detail.value = null; detailError.value = ''
  try { const result = await getSpu(id); if (!scope.current(captured) || ticket !== detailSequence || !detailOpen.value) return; if (result.merchant_id !== scope.auth.user?.merchant_id) throw new Error('商品不属于本商家。'); detail.value = result }
  catch (cause) { if (scope.current(captured) && ticket === detailSequence) detailError.value = scope.explain(cause) }
  finally { if (scope.current(captured) && ticket === detailSequence) detailLoading.value = false }
}
async function refreshIdentity() { try { await scope.auth.refreshIdentity() } catch (cause) { error.value = scope.explain(cause) } }
function closeDetail() { if (!detailOpen.value) { detailSequence++; detail.value = null; detailError.value = '' } }
watch(scope.revision, () => { if (isOwner.value || isEmployee.value) void load(0) }, { flush: 'post' })
watch(() => route.name, (next, previous) => { if (next !== previous && !loading.value) void load(0) })
onMounted(() => { void load() })
</script>
<template>
  <div class="merchant-page workspace-page">
    <div class="welcome-row"><div><div class="page-eyebrow">SHOP WORKSPACE</div><h1>{{ title }}</h1><p>当前店铺 {{ scope.auth.user?.shop_id ?? '—' }} · 商家 {{ scope.auth.user?.merchant_id ?? '—' }}</p></div><el-button :loading="loading" :disabled="busy" @click="load()">刷新</el-button></div>
    <p v-if="error" class="workspace-error" role="alert">{{ error }} <el-button link @click="load()">重试</el-button><el-button v-if="denied" link @click="refreshIdentity">重新验证身份</el-button></p><p v-if="notice" class="workspace-success" role="status">{{ notice }}</p>
    <section class="merchant-card">
      <p v-if="loading" class="list-state">正在加载…</p>
      <template v-else-if="mode === 'home'">
        <template v-if="isEmployee"><h2>员工工作台</h2><p>员工编号 {{ scope.auth.user?.id ?? '—' }} · 店铺 {{ scope.auth.user?.shop_id ?? '—' }} · 商家 {{ scope.auth.user?.merchant_id ?? '—' }}</p><p>你可以只读查看同商家的商品目录；共享库存需由商家管理员审批店主提交的仓库使用申请后才能查看。</p><div class="workspace-links"><RouterLink to="/employee/catalog">商品目录（只读）</RouterLink><RouterLink to="/employee/inventory">共享库存（只读）</RouterLink></div></template>
        <template v-else-if="shop"><h2>{{ shop.name }}</h2><p>店铺编号 {{ shop.id }} · 负责人 {{ shop.owner_user_id }}</p><p>创建时间 {{ formatNaiveDateTime(shop.created_at) }}</p><div class="workspace-links"><RouterLink to="/shop/employees">员工管理</RouterLink><RouterLink to="/shop/applications">入职审批</RouterLink><RouterLink to="/shop/roles">岗位管理</RouterLink><RouterLink to="/shop/warehouse">仓库授权</RouterLink><RouterLink to="/shop/catalog">商品只读</RouterLink><RouterLink to="/shop/inventory">库存只读</RouterLink><RouterLink to="/shop/owner-transfer">负责人交接</RouterLink></div></template><p v-else class="list-state">未读取到当前店铺。</p>
      </template>
      <template v-else-if="mode === 'warehouse'"><p v-if="!isOwner">员工无权查看或提交仓库授权申请，请联系本店负责人。</p><template v-else><h2>申请使用商家共享仓库</h2><p>服务器根据当前店主的真实店铺确定仓库；不指定仓库或其他店铺。批准只代表使用资格，不代表可以人工调整库存。</p><el-button type="primary" :loading="busy" @click="submit">提交仓库授权申请</el-button><p v-if="!applications?.items.length" class="list-state">暂无申请记录。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>申请编号</th><th>状态</th><th>驳回原因</th><th>申请时间</th></tr></thead><tbody><tr v-for="row in applications.items" :key="row.id"><td>{{ row.id }}</td><td>{{ statusName(row.status) }}</td><td>{{ row.rejection_reason ?? '—' }}</td><td>{{ formatNaiveDateTime(row.created_at) }}</td></tr></tbody></table></div><PageButtons :page="applications" :busy="busy || loading" @change="load" /><RouterLink to="/shop/inventory">查询授权后的库存</RouterLink></template></template>
      <template v-else-if="mode === 'catalog'"><h2>商家共享目录</h2><p>商品与 SKU 价格由商家管理员维护，本页只读。</p><p v-if="!products?.items.length" class="list-state">暂无商品。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>商品</th><th>分类编号</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in products.items" :key="row.id"><td>{{ row.name }}</td><td>{{ row.category_id }}</td><td>{{ row.status }}</td><td><el-button link @click="openDetail(row.id)">查看详情</el-button></td></tr></tbody></table></div><PageButtons :page="products" :busy="loading" @change="load" /><h2>一级分类</h2><p v-if="!categories?.items.length">暂无分类。</p><p v-for="row in categories?.items" :key="row.id">{{ row.name }} · {{ row.id }}</p><PageButtons :page="categories" :busy="loading" @change="categoryOffset = $event; load()" /></template>
      <template v-else>
        <template v-if="isEmployee && denied"><p>本店未向你授权共享仓库库存，需由商家管理员审批店主提交的仓库使用申请后才能读取，请联系本店负责人。</p></template>
        <template v-else><template v-if="warehouse"><h2>{{ warehouse.name }}</h2><p>共享仓库 {{ warehouse.id }}，库存不是本店独立库存。本店不能进行人工库存调整。</p></template><p v-if="denied">本店尚未取得有效仓库使用资格。<RouterLink to="/shop/warehouse">前往仓库授权申请</RouterLink></p><p v-else-if="!stocks?.items.length" class="list-state">暂无可读取库存。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>商品</th><th>SKU</th><th>规格</th><th>真实库存</th></tr></thead><tbody><tr v-for="row in stocks.items" :key="row.sku_id"><td>{{ row.spu_name }}</td><td>{{ row.sku_code }} · {{ row.sku_id }}</td><td>{{ row.specification }}</td><td>{{ row.quantity }}</td></tr></tbody></table></div><PageButtons :page="stocks" :busy="loading" @change="load" /></template>
      </template>
    </section>
    <ElDialog v-model="detailOpen" title="商品详情（只读）" width="min(720px, calc(100vw - 32px))" @closed="closeDetail"><p v-if="detailLoading">正在加载详情…</p><p v-else-if="detailError" class="workspace-error" role="alert">{{ detailError }} <el-button @click="openDetail(detailId)">重试</el-button></p><template v-else-if="detail"><h2>{{ detail.name }}</h2><p>{{ detail.description ?? '暂无描述' }} · {{ detail.status }}</p><p v-if="!detail.skus.length">暂无 SKU。</p><div class="table-wrap"><table class="merchant-table"><thead><tr><th>编码</th><th>规格</th><th>价格</th></tr></thead><tbody><tr v-for="sku in detail.skus" :key="sku.id"><td>{{ sku.sku_code }}</td><td>{{ sku.specification }}</td><td>¥{{ formatDecimal(sku.price) }}</td></tr></tbody></table></div></template></ElDialog>
  </div>
</template>
