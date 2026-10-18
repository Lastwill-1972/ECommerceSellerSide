<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as api from '../api'
import { listShopApplications } from '@/modules/merchant/api'
import { useIdentityScope } from '../useIdentityScope'
import PageButtons from '../components/PageButtons.vue'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { Page, ShopApplication } from '@/shared/types/api'
type Kind = 'merchant' | 'shop' | 'employee'
type Application = api.MerchantApplication | ShopApplication | api.EmployeeApplication
const router = useRouter(), kind = ref<Kind>('merchant'), status = ref(''), offset = ref(0)
const records = ref<Page<Application> | null>(null), merchants = ref<Page<api.DirectoryMerchant> | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const form = reactive({ merchant_name: '', merchant_id: '', shop_name: '', shop_id: '' })
let sequence = 0, directorySequence = 0
const scope = useIdentityScope(['NONE'], () => { sequence++; directorySequence++; records.value = null; merchants.value = null; error.value = ''; notice.value = ''; offset.value = 0; busy.value = false; loading.value = false; Object.assign(form, { merchant_name: '', merchant_id: '', shop_name: '', shop_id: '' }) })
async function load(next = offset.value) {
  const captured = scope.capture(), request = ++sequence; records.value = null; loading.value = true; error.value = ''; offset.value = next
  try {
    const page = kind.value === 'merchant' ? await api.merchantApplications(20, next, status.value || undefined) : kind.value === 'shop' ? await listShopApplications(20, next, status.value || undefined) : await api.employeeApplications(20, next, status.value || undefined)
    if (!scope.current(captured) || request !== sequence) return
    if (page.items.some(row => row.applicant_user_id !== scope.auth.user?.id)) throw new Error('返回了其他申请人的记录，已隐藏。')
    records.value = page
  } catch (cause) { if (scope.current(captured) && request === sequence) error.value = scope.explain(cause) }
  finally { if (scope.current(captured) && request === sequence) loading.value = false }
}
async function loadDirectory(next = 0) {
  const captured = scope.capture(), request = ++directorySequence
  try { const result = await api.directory(20, next); if (scope.current(captured) && request === directorySequence) merchants.value = result }
  catch (cause) { if (scope.current(captured) && request === directorySequence) error.value = scope.explain(cause) }
}
async function submit() {
  if (busy.value || scope.auth.user?.role !== 'NONE') return
  if ((kind.value === 'merchant' && !api.validText(form.merchant_name, 255)) || (kind.value === 'shop' && (!api.validId(form.merchant_id) || !api.validText(form.shop_name, 255))) || (kind.value === 'employee' && !api.validId(form.shop_id))) { error.value = '请填写有效的名称（最多255个字符）及准确的正整数编号。'; return }
  const captured = scope.capture(); busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = kind.value === 'merchant' ? await api.applyMerchant(form.merchant_name.trim()) : kind.value === 'shop' ? await api.applyShop(form.merchant_id, form.shop_name.trim()) : await api.applyEmployee(form.shop_id)
    if (!scope.current(captured)) return
    notice.value = `申请已提交，当前状态：${api.statusName(result.status)}。提交不代表已获得身份。`; status.value = ''; await load(0)
  } catch (cause) { if (scope.current(captured)) error.value = scope.explain(cause) }
  finally { if (scope.current(captured)) busy.value = false }
}
async function refresh() {
  if (busy.value) return
  const token = scope.auth.token, userId = scope.auth.user?.id; busy.value = true
  try {
    const user = await scope.auth.refreshIdentity()
    if (!user || scope.auth.token !== token || user.id !== userId) return
    if (user.role !== 'NONE') { await router.replace({ name: 'dashboard' }); return }
    await Promise.all([load(0), loadDirectory()])
  } catch (cause) { if (scope.auth.token === token) error.value = scope.explain(cause) }
  finally { if (scope.auth.token === token) busy.value = false }
}
watch(kind, () => { records.value = null; notice.value = ''; error.value = ''; void load(0) })
watch(status, () => { void load(0) })
watch(scope.revision, () => { if (scope.auth.user?.role === 'NONE') void Promise.all([load(0), loadDirectory()]) }, { flush: 'post' })
onMounted(() => { void Promise.all([load(), loadDirectory()]) })
</script>
<template>
  <div class="merchant-page workspace-page">
    <div class="welcome-row"><div><div class="page-eyebrow">APPLICATION CENTER</div><h1>身份申请中心</h1><p>申请通过后由真实业务关系授予身份；刷新申请与身份以确认结果。</p></div><el-button :loading="busy" @click="refresh">刷新申请与身份</el-button></div>
    <div class="workspace-tabs"><el-button v-for="(label, key) in { merchant: '商家入驻', shop: '开店申请', employee: '员工入职' }" :key="key" :disabled="busy" :type="kind === key ? 'primary' : 'default'" @click="kind = key">{{ label }}</el-button></div>
    <p v-if="error" class="workspace-error" role="alert">{{ error }} <el-button link @click="load(); loadDirectory()">重试</el-button></p><p v-if="notice" class="workspace-success" role="status">{{ notice }}</p>
    <section class="merchant-card">
      <h2>提交申请</h2><form class="workspace-form" @submit.prevent="submit">
        <label v-if="kind === 'merchant'">商家名称<input v-model="form.merchant_name" aria-label="商家名称" :disabled="busy" /></label>
        <template v-if="kind === 'shop'"><label>目标商家<select v-model="form.merchant_id" aria-label="目标商家" :disabled="busy"><option value="">请选择当前目录页中的商家</option><option v-for="item in merchants?.items" :key="item.id" :value="item.id">{{ item.name }} · {{ item.id }}</option></select></label><label>拟建店铺名称<input v-model="form.shop_name" aria-label="拟建店铺名称" :disabled="busy" /></label><PageButtons :page="merchants" :busy="busy" @change="loadDirectory" /></template>
        <label v-if="kind === 'employee'">目标店铺编号<input v-model="form.shop_id" aria-label="目标店铺编号" inputmode="numeric" :disabled="busy" /><small>请向店铺负责人索取准确编号；当前没有公开店铺搜索目录。</small></label>
        <el-button native-type="submit" type="primary" :loading="busy">提交申请</el-button>
      </form>
    </section>
    <section class="merchant-card">
      <h2>个人申请记录</h2><label>申请状态 <select v-model="status" aria-label="申请状态" :disabled="busy"><option value="">全部状态</option><option value="PENDING">待审核</option><option value="APPROVED">已批准</option><option value="REJECTED">已驳回</option></select></label>
      <p v-if="loading" class="list-state">正在加载申请…</p><p v-else-if="!records?.items.length" class="list-state">暂无申请记录。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>申请编号</th><th>申请内容</th><th>状态</th><th>驳回原因</th><th>申请时间</th></tr></thead><tbody><tr v-for="row in records.items" :key="row.id"><td>{{ row.id }}</td><td>{{ 'merchant_name' in row ? row.merchant_name : 'shop_name' in row ? row.shop_name : `店铺 ${row.shop_id}` }}</td><td>{{ api.statusName(row.status) }}</td><td>{{ row.rejection_reason ?? '—' }}</td><td>{{ formatNaiveDateTime(row.created_at) }}</td></tr></tbody></table></div><PageButtons :page="records" :busy="loading || busy" @change="load" />
    </section>
  </div>
</template>
