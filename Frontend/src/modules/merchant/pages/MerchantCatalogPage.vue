<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ElAlert, ElCard, ElDialog, ElEmpty, ElForm, ElFormItem, ElMessage, ElMessageBox, ElOption, ElPagination, ElSelect, ElSkeleton, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
import * as api from '@/modules/merchant/catalogApi'
import { formatNaiveDateTime, formatDecimal } from '@/shared/http/precision'
import type { Category, OffshelfApplication, Page, Spu, SpuDetail, Sku } from '@/shared/types/api'

const categories = ref<Page<Category> | null>(null), spus = ref<Page<Spu> | null>(null)
const applications = ref<Page<OffshelfApplication> | null>(null), detail = ref<SpuDetail | null>(null)
const loading = ref(false), saving = ref(false), detailLoading = ref(false), error = ref(''), detailError = ref(''), detailId = ref('')
const categoryOffset = ref(0), spuOffset = ref(0), appOffset = ref(0), categoryFilter = ref(''), statusFilter = ref('')
const categoryDialog = ref(false), spuDialog = ref(false), skuDialog = ref(false), detailVisible = ref(false)
const editingSpu = ref(false), editingSpuId = ref(''), editingSku = ref(false), selectedSku = ref<Sku | null>(null)
const categoryForm = reactive({ name: '' })
const spuForm = reactive({ category_id: '', name: '', description: '' })
const skuForm = reactive({ sku_code: '', specification: '', price: '' })
let sequence = 0, detailSequence = 0
const scope = useMerchantScope(() => {
  sequence += 1; detailSequence += 1; categories.value = null; spus.value = null; applications.value = null; detail.value = null; detailId.value = ''; detailError.value = ''
  error.value = ''; categoryOffset.value = 0; spuOffset.value = 0; appOffset.value = 0; categoryFilter.value = ''; statusFilter.value = ''
  categoryDialog.value = false; spuDialog.value = false; skuDialog.value = false; detailVisible.value = false
  categoryForm.name = ''; spuForm.category_id = ''; spuForm.name = ''; spuForm.description = ''
  skuForm.sku_code = ''; skuForm.specification = ''; skuForm.price = ''; editingSpuId.value = ''; editingSpu.value = false
  selectedSku.value = null; saving.value = false; loading.value = false; detailLoading.value = false
})

async function load() {
  const request = ++sequence, captured = scope.capture()
  loading.value = true; error.value = ''
  try {
    const [categoryPage, spuPage, applicationPage] = await Promise.all([
      api.listCategories(100, categoryOffset.value),
      api.listSpus(20, spuOffset.value, { ...(categoryFilter.value ? { category_id: categoryFilter.value } : {}), ...(statusFilter.value ? { status: statusFilter.value } : {}) }),
      api.listOffshelfApplications(10, appOffset.value),
    ])
    if (!scope.current(captured) || request !== sequence) return
    if (categoryPage.items.some((item) => item.merchant_id !== scope.auth.user?.merchant_id) || spuPage.items.some((item) => item.merchant_id !== scope.auth.user?.merchant_id) || applicationPage.items.some((item) => item.spu.merchant_id !== scope.auth.user?.merchant_id)) throw new Error('服务器返回了其他商家的商品数据。')
    categories.value = categoryPage; spus.value = spuPage; applications.value = applicationPage
  } catch (cause) { if (!scope.current(captured) || request !== sequence) return; const message = await scope.explain(cause); if (scope.current(captured) && request === sequence) error.value = message }
  finally { if (scope.current(captured) && request === sequence) loading.value = false }
}
async function loadDetail(id: string) {
  detailId.value = id; detailError.value = ''
  const request = ++detailSequence, captured = scope.capture(); detailLoading.value = true; detail.value = null
  try {
    const result = await api.getSpu(id)
    if (!scope.current(captured) || request !== detailSequence) return
    if (result.merchant_id !== scope.auth.user?.merchant_id) throw new Error('商品不属于当前商家。')
    detail.value = result
  } catch (cause) { if (!scope.current(captured) || request !== detailSequence) return; const message = await scope.explain(cause); if (scope.current(captured) && request === detailSequence) { detailError.value = message; ElMessage.error(message) } }
  finally { if (scope.current(captured) && request === detailSequence) detailLoading.value = false }
}
function openDetail(id: string) { detailVisible.value = true; void loadDetail(id) }
function retryDetail() { if (detailId.value && detailVisible.value && !detailLoading.value) void loadDetail(detailId.value) }
function clearClosedDetail() {
  if (detailVisible.value) return
  detailSequence += 1; detail.value = null; detailId.value = ''; detailError.value = ''; detailLoading.value = false
}
function changePage(which: 'category' | 'spu' | 'app', offset: number) {
  if (which === 'category') categoryOffset.value = offset
  if (which === 'spu') spuOffset.value = offset
  if (which === 'app') appOffset.value = offset
  void load()
}
watch([categoryFilter, statusFilter], () => { spuOffset.value = 0; void load() })
watch(scope.revision, () => { if (scope.auth.user?.merchant_id) void load() }, { flush: 'post' })
onMounted(() => { void load() })

async function createCategory() {
  if (saving.value) return
  const name = categoryForm.name.trim()
  if (!name || [...name].length > 255) { ElMessage.error('分类名称必填，且最多 255 个字符。'); return }
  const captured = scope.capture(); saving.value = true
  try { await api.createCategory(name); if (!scope.current(captured)) return; categoryDialog.value = false; categoryForm.name = ''; ElMessage.success('分类已创建。'); await load() }
  catch (cause) { if (!scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) ElMessage.error(message) }
  finally { if (scope.current(captured)) saving.value = false }
}
function openSpu(row?: Spu) {
  editingSpu.value = Boolean(row); editingSpuId.value = row?.id ?? ''; spuForm.category_id = row?.category_id ?? categories.value?.items[0]?.id ?? ''
  spuForm.name = row?.name ?? ''; spuForm.description = row?.description ?? ''; spuDialog.value = true
}
async function saveSpu() {
  if (saving.value) return
  if (!spuForm.category_id || !spuForm.name.trim() || [...spuForm.name.trim()].length > 255 || [...spuForm.description].length > 10000) { ElMessage.error('请填写有效分类和商品名称；描述最多 10000 个字符。'); return }
  const captured = scope.capture(); saving.value = true
  try {
    if (editingSpu.value) await api.updateSpu(editingSpuId.value, { category_id: spuForm.category_id, name: spuForm.name.trim(), description: spuForm.description || null })
    else await api.createSpu({ category_id: spuForm.category_id, name: spuForm.name.trim(), description: spuForm.description || null })
    if (!scope.current(captured)) return
    spuDialog.value = false; ElMessage.success(editingSpu.value ? '商品信息已更新。' : '商品已创建。')
    if (detail.value?.id === editingSpuId.value && editingSpu.value) await loadDetail(detail.value.id)
    await load()
  } catch (cause) { if (!scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) ElMessage.error(message) }
  finally { if (scope.current(captured)) saving.value = false }
}
function openSku(row?: Sku) {
  selectedSku.value = row ?? null; editingSku.value = Boolean(row)
  skuForm.sku_code = row?.sku_code ?? ''; skuForm.specification = row?.specification ?? ''; skuForm.price = row?.price ?? ''
  skuDialog.value = true
}
async function saveSku() {
  if (saving.value) return
  if (!detail.value || !skuForm.sku_code.trim() || !skuForm.specification.trim() || [...skuForm.sku_code.trim()].length > 100 || [...skuForm.specification.trim()].length > 1000 || !/^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$/.test(skuForm.price)) { ElMessage.error('请填写有效 SKU 编码（最多 100 个字符）、规格（最多 1000 个字符）和不超过 10 位整数的非负价格（最多两位小数）。'); return }
  const captured = scope.capture(); saving.value = true
  try {
    const payload = { sku_code: skuForm.sku_code.trim(), specification: skuForm.specification.trim(), price: skuForm.price }
    if (editingSku.value && selectedSku.value) await api.updateSku(selectedSku.value.id, payload)
    else await api.createSku(detail.value.id, payload)
    if (!scope.current(captured)) return
    skuDialog.value = false; ElMessage.success(editingSku.value ? 'SKU 已更新。' : 'SKU 已创建。')
    await loadDetail(detail.value.id); await load()
  } catch (cause) { if (!scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) ElMessage.error(message) }
  finally { if (scope.current(captured)) saving.value = false }
}
async function publish() {
  if (saving.value) return
  if (!detail.value || detail.value.skus.length === 0) { ElMessage.error('请先为商品创建至少一个 SKU。'); return }
  const captured = scope.capture(); saving.value = true
  try { await api.publishSpu(detail.value.id); if (!scope.current(captured)) return; ElMessage.success('商品已上架。'); await loadDetail(detail.value.id); await load() }
  catch (cause) { if (!scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) ElMessage.error(message) }
  finally { if (scope.current(captured)) saving.value = false }
}
async function requestOffshelf(row: Spu) {
  if (saving.value) return
  const captured = scope.capture(); saving.value = true
  try {
    await ElMessageBox.confirm('此操作只会提交平台审核申请，不会直接下架或删除商品。', '申请下架', { confirmButtonText: '提交申请', cancelButtonText: '取消', type: 'warning' })
    if (!scope.current(captured)) return
    await api.submitOffshelfApplication(row.id); if (!scope.current(captured)) return; ElMessage.success('下架申请已提交。'); await load()
  }
  catch (cause) { if (cause === 'cancel' || cause === 'close' || !scope.current(captured)) return; const message = await scope.explain(cause); if (scope.current(captured)) ElMessage.error(message) }
  finally { if (scope.current(captured)) saving.value = false }
}
function labelStatus(status: string) { return ({ OFF_SALE: '未上架', ON_SALE: '销售中', PENDING: '待审核', APPROVED: '已批准', REJECTED: '已驳回' } as Record<string, string>)[status] ?? status }
</script>

<template>
  <div class="merchant-page catalog-page">
    <div class="merchant-page-heading"><div><span class="section-kicker">CATALOG</span><h1>商品管理</h1><p>维护商品分类、商品资料、SKU 和下架申请记录。</p></div><div class="page-actions"><el-button @click="categoryDialog = true">新建分类</el-button><el-button type="primary" :disabled="!categories?.items.length" @click="openSpu()">新建商品</el-button></div></div>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error"><template #default><el-button text @click="load">重新加载</el-button></template></el-alert>
    <el-card class="merchant-card">
      <template #header><div class="card-heading"><strong>商品列表</strong><div class="filters"><el-select v-model="categoryFilter" placeholder="全部分类" clearable><el-option v-for="item in categories?.items ?? []" :key="item.id" :label="item.name" :value="item.id" /></el-select><el-select v-model="statusFilter" placeholder="全部状态" clearable><el-option label="未上架" value="OFF_SALE" /><el-option label="销售中" value="ON_SALE" /></el-select><el-button :loading="loading" @click="load">刷新</el-button></div></div></template>
      <el-skeleton v-if="loading && !spus" :rows="5" animated />
      <el-empty v-else-if="!spus?.items.length" description="暂无商品"><el-button type="primary" @click="openSpu()">创建第一个商品</el-button></el-empty>
      <el-table v-else :data="spus.items" row-key="id" @row-click="(row) => openDetail((row as Spu).id)"><el-table-column prop="name" label="商品名称" min-width="180" /><el-table-column label="分类" min-width="120"><template #default="{ row }">{{ categories?.items.find((item) => item.id === row.category_id)?.name ?? `分类 ${row.category_id}` }}</template></el-table-column><el-table-column label="状态" width="110"><template #default="{ row }">{{ labelStatus(row.status) }}</template></el-table-column><el-table-column label="更新时间" min-width="170"><template #default="{ row }">{{ formatNaiveDateTime(row.updated_at) }}</template></el-table-column><el-table-column label="操作" width="210" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="openDetail(row.id)">详情</el-button><el-button link @click.stop="openSpu(row as Spu)">编辑</el-button><el-button v-if="row.status === 'ON_SALE'" link type="warning" :loading="saving" @click.stop="requestOffshelf(row as Spu)">申请下架</el-button></template></el-table-column></el-table>
      <div v-if="spus && spus.total > 20" class="pagination-row"><el-pagination background layout="total, prev, pager, next" :page-size="20" :current-page="Math.floor(spuOffset / 20) + 1" :total="spus.total" @current-change="(page: number) => changePage('spu', (page - 1) * 20)" /></div>
    </el-card>
    <div class="catalog-lower-grid">
      <el-card class="merchant-card"><template #header><div class="card-heading"><strong>商品分类</strong><el-button link :loading="loading" @click="changePage('category', 0)">刷新</el-button></div></template><el-skeleton v-if="loading && !categories" :rows="3" animated /><el-empty v-else-if="!categories?.items.length" description="暂无分类，请先创建分类" /><div v-else class="catalog-category-list"><div v-for="item in categories.items" :key="item.id"><span>{{ item.name }}</span><small>{{ item.id }}</small></div></div><div v-if="categories && categories.total > 100" class="pagination-row"><el-pagination small layout="prev, pager, next" :page-size="100" :current-page="Math.floor(categoryOffset / 100) + 1" :total="categories.total" @current-change="(page: number) => changePage('category', (page - 1) * 100)" /></div></el-card>
      <el-card class="merchant-card"><template #header><div class="card-heading"><strong>下架申请历史</strong><el-button link :loading="loading" @click="changePage('app', 0)">刷新</el-button></div></template><el-skeleton v-if="loading && !applications" :rows="3" animated /><el-empty v-else-if="!applications?.items.length" description="暂无下架申请" /><div v-else class="offshelf-list"><article v-for="item in applications.items" :key="item.id"><div><strong>{{ item.spu.name }}</strong><small>{{ formatNaiveDateTime(item.created_at) }}</small></div><el-tag>{{ labelStatus(item.status) }}</el-tag><p v-if="item.rejection_reason">驳回原因：{{ item.rejection_reason }}</p></article></div><div v-if="applications && applications.total > 10" class="pagination-row"><el-pagination small layout="prev, pager, next" :page-size="10" :current-page="Math.floor(appOffset / 10) + 1" :total="applications.total" @current-change="(page: number) => changePage('app', (page - 1) * 10)" /></div></el-card>
    </div>

    <el-dialog v-model="categoryDialog" title="新建分类" width="min(460px, calc(100vw - 32px))"><el-form label-position="top"><el-form-item label="分类名称"><el-input v-model="categoryForm.name" maxlength="255" show-word-limit /></el-form-item></el-form><template #footer><el-button @click="categoryDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="createCategory">创建</el-button></template></el-dialog>
    <el-dialog v-model="spuDialog" :title="editingSpu ? '编辑商品' : '新建商品'" width="min(520px, calc(100vw - 32px))"><el-form label-position="top"><el-form-item label="商品分类"><el-select v-model="spuForm.category_id" placeholder="选择分类"><el-option v-for="item in categories?.items ?? []" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="商品名称"><el-input v-model="spuForm.name" maxlength="255" /></el-form-item><el-form-item label="描述"><el-input v-model="spuForm.description" type="textarea" :rows="4" maxlength="10000" show-word-limit /></el-form-item></el-form><template #footer><el-button @click="spuDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveSpu">保存</el-button></template></el-dialog>
    <el-dialog v-model="detailVisible" :title="detail?.name ?? '商品详情'" width="min(760px, calc(100vw - 32px))" @closed="clearClosedDetail"><el-skeleton v-if="detailLoading" :rows="4" animated /><div v-else-if="detailError" class="detail-load-error"><el-alert type="error" :closable="false" show-icon :title="detailError" /><el-button type="primary" @click="retryDetail">重试</el-button></div><template v-else-if="detail"><div class="detail-summary"><el-tag>{{ labelStatus(detail.status) }}</el-tag><span>分类：{{ categories?.items.find((item) => item.id === detail?.category_id)?.name ?? detail.category_id }}</span><el-button @click="openSpu(detail)">编辑商品</el-button><el-button v-if="detail.status === 'OFF_SALE'" type="primary" :disabled="detail.skus.length === 0" :loading="saving" @click="publish">上架</el-button><span v-if="detail.status === 'OFF_SALE' && !detail.skus.length" class="muted-note">创建至少一个 SKU 后才能上架</span></div><p class="detail-description">{{ detail.description || '暂无描述' }}</p><div class="card-heading sku-heading"><strong>SKU 列表（{{ detail.skus.length }}）</strong><el-button type="primary" @click="openSku()">新建 SKU</el-button></div><el-empty v-if="!detail.skus.length" description="暂无 SKU" /><el-table v-else :data="detail.skus" row-key="id"><el-table-column prop="sku_code" label="SKU 编码" /><el-table-column prop="specification" label="规格" /><el-table-column label="价格"><template #default="{ row }">¥ {{ formatDecimal(row.price) }}</template></el-table-column><el-table-column label="操作"><template #default="{ row }"><el-button link type="primary" @click="openSku(row as Sku)">编辑</el-button></template></el-table-column></el-table></template></el-dialog>
    <el-dialog v-model="skuDialog" :title="editingSku ? '编辑 SKU' : '新建 SKU'" width="min(500px, calc(100vw - 32px))"><el-form label-position="top"><el-form-item label="SKU 编码"><el-input v-model="skuForm.sku_code" maxlength="100" /></el-form-item><el-form-item label="规格"><el-input v-model="skuForm.specification" maxlength="1000" /></el-form-item><el-form-item label="价格"><el-input v-model="skuForm.price" inputmode="decimal" placeholder="例如 129.90" /></el-form-item></el-form><template #footer><el-button @click="skuDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveSku">保存</el-button></template></el-dialog>
  </div>
</template>
